from __future__ import annotations

import ast
import hashlib
import json
import re
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import yaml

from .discovery import decode_source_bytes
from .domain import (
    ConfigurationReference,
    DependencyArtifact,
    SourceFile,
    SourceRelation,
    SourceSymbol,
)


def _read_source_text(path: Path) -> str:
    return decode_source_bytes(path.read_bytes())[0]


@dataclass
class AnalysisFacts:
    symbols: list[SourceSymbol] = field(default_factory=list)
    relations: list[SourceRelation] = field(default_factory=list)
    configurations: list[ConfigurationReference] = field(default_factory=list)
    dependencies: list[DependencyArtifact] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def merge(self, other: AnalysisFacts) -> None:
        self.symbols.extend(other.symbols)
        self.relations.extend(other.relations)
        self.configurations.extend(other.configurations)
        self.dependencies.extend(other.dependencies)
        self.warnings.extend(other.warnings)


class AnalyzerPlugin(Protocol):
    name: str

    def supports(self, source: SourceFile) -> bool: ...

    def analyze(self, root: Path, source: SourceFile) -> AnalysisFacts: ...


def _signature(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _symbol(
    source: SourceFile,
    name: str,
    symbol_type: str,
    start: int,
    end: int,
    *,
    metadata: dict | None = None,
) -> SourceSymbol:
    return SourceSymbol(
        relative_path=source.relative_path,
        symbol=name,
        symbol_type=symbol_type,
        start_line=max(1, start),
        end_line=max(start, end),
        signature_hash=_signature(f"{symbol_type}:{name}"),
        metadata=metadata or {},
    )


class PythonAnalyzer:
    name = "python-ast"

    def supports(self, source: SourceFile) -> bool:
        return source.language == "Python"

    def analyze(self, root: Path, source: SourceFile) -> AnalysisFacts:
        facts = AnalysisFacts()
        path = root / source.relative_path
        try:
            tree = ast.parse(_read_source_text(path), filename=source.relative_path)
        except (OSError, SyntaxError, UnicodeError) as exc:
            facts.warnings.append(f"{source.relative_path}:python_parse:{type(exc).__name__}")
            return facts

        scope: list[str] = []

        class Visitor(ast.NodeVisitor):
            def visit_ClassDef(self, node: ast.ClassDef) -> None:
                qualified = ".".join([*scope, node.name])
                facts.symbols.append(
                    _symbol(source, qualified, "class", node.lineno, node.end_lineno or node.lineno)
                )
                for base in node.bases:
                    target = ast.unparse(base)
                    facts.relations.append(
                        SourceRelation(
                            qualified,
                            target,
                            "EXTENDS",
                            "STATIC_CONFIRMED",
                            source.relative_path,
                            node.lineno,
                        )
                    )
                scope.append(node.name)
                self.generic_visit(node)
                scope.pop()

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                qualified = ".".join([*scope, node.name])
                symbol_type = "method" if scope else "function"
                facts.symbols.append(
                    _symbol(
                        source,
                        qualified,
                        symbol_type,
                        node.lineno,
                        node.end_lineno or node.lineno,
                    )
                )
                scope.append(node.name)
                self.generic_visit(node)
                scope.pop()

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_Call(self, node: ast.Call) -> None:
                if scope:
                    try:
                        target = ast.unparse(node.func)
                    except ValueError:
                        target = "<dynamic>"
                    lowered = target.lower()
                    relation_type = "CALLS"
                    if any(token in lowered for token in ("getenv", "environ.get", "settings.")):
                        relation_type = "READS_CONFIG"
                    elif any(token in lowered for token in ("httpx.", "requests.", "urllib.")):
                        relation_type = "SENDS_HTTP"
                    elif any(
                        token in lowered
                        for token in (".commit", ".add", ".delete", ".update")
                    ):
                        relation_type = "WRITES_DB"
                    elif any(
                        token in lowered
                        for token in (".execute", ".scalar", ".scalars", ".query")
                    ):
                        relation_type = "READS_DB"
                    elif "retry" in lowered:
                        relation_type = "RETRIES"
                    facts.relations.append(
                        SourceRelation(
                            ".".join(scope),
                            target,
                            relation_type,
                            "STATIC_CONFIRMED",
                            source.relative_path,
                            node.lineno,
                        )
                    )
                self.generic_visit(node)

            def visit_Raise(self, node: ast.Raise) -> None:
                if scope:
                    target = ast.unparse(node.exc) if node.exc is not None else "re-raise"
                    facts.relations.append(
                        SourceRelation(
                            ".".join(scope),
                            target,
                            "THROWS",
                            "STATIC_CONFIRMED",
                            source.relative_path,
                            node.lineno,
                        )
                    )
                self.generic_visit(node)

            def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
                if scope:
                    target = ast.unparse(node.type) if node.type is not None else "Exception"
                    facts.relations.append(
                        SourceRelation(
                            ".".join(scope),
                            target,
                            "HANDLES_EXCEPTION",
                            "STATIC_CONFIRMED",
                            source.relative_path,
                            node.lineno,
                        )
                    )
                self.generic_visit(node)

            def visit_Import(self, node: ast.Import) -> None:
                source_symbol = ".".join(scope) or source.relative_path
                for item in node.names:
                    facts.relations.append(
                        SourceRelation(
                            source_symbol,
                            item.name,
                            "IMPORTS",
                            "STATIC_CONFIRMED",
                            source.relative_path,
                            node.lineno,
                        )
                    )

            def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
                source_symbol = ".".join(scope) or source.relative_path
                facts.relations.append(
                    SourceRelation(
                        source_symbol,
                        node.module or "",
                        "IMPORTS",
                        "STATIC_CONFIRMED",
                        source.relative_path,
                        node.lineno,
                    )
                )

        Visitor().visit(tree)
        return facts


class RegexCodeAnalyzer:
    name = "bounded-regex-code"
    _supported = {"Java", "JavaScript", "TypeScript", "shell", "SQL", "Gradle", "Kotlin"}
    _declaration_patterns = {
        "Java": re.compile(
            r"\b(class|interface|enum|record)\s+([A-Za-z_$][\w$]*)|"
            r"(?:public|protected|private|static|final|synchronized|\s)+"
            r"[\w<>\[\],.?]+\s+([A-Za-z_$][\w$]*)\s*\("
        ),
        "JavaScript": re.compile(
            r"\b(?:class|function)\s+([A-Za-z_$][\w$]*)|"
            r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\("
        ),
        "TypeScript": re.compile(
            r"\b(?:class|interface|enum|function|type)\s+([A-Za-z_$][\w$]*)|"
            r"\b(?:const|let)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\("
        ),
        "shell": re.compile(r"^\s*(?:function\s+)?([A-Za-z_][\w]*)\s*\(\s*\)"),
        "SQL": re.compile(
            r"\b(?:create\s+(?:or\s+replace\s+)?)(function|procedure|view|table)\s+"
            r"([A-Za-z_][\w.]*)",
            re.IGNORECASE,
        ),
        "Gradle": re.compile(r"^\s*(?:task|register)\s*\(?[\"']?([A-Za-z_][\w-]*)"),
        "Kotlin": re.compile(r"\b(?:class|interface|object|fun)\s+([A-Za-z_][\w]*)"),
    }
    _call = re.compile(r"\b([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*\(")
    _script_import = re.compile(
        r"""(?:from\s+|import\s*\(|require\s*\()\s*["']([^"']+)["']"""
    )
    _jvm_import = re.compile(r"^\s*import\s+([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+)")

    def supports(self, source: SourceFile) -> bool:
        return source.language in self._supported

    def analyze(self, root: Path, source: SourceFile) -> AnalysisFacts:
        facts = AnalysisFacts()
        lines = _read_source_text(root / source.relative_path).splitlines()
        pattern = self._declaration_patterns[source.language]
        current_symbol = source.relative_path
        for number, line in enumerate(lines, 1):
            if source.language in {"JavaScript", "TypeScript"}:
                for imported in self._script_import.findall(line):
                    facts.relations.append(
                        SourceRelation(
                            current_symbol,
                            imported,
                            "IMPORTS",
                            "STATIC_CONFIRMED",
                            source.relative_path,
                            number,
                        )
                    )
            elif source.language in {"Java", "Kotlin"}:
                imported = self._jvm_import.search(line)
                if imported:
                    facts.relations.append(
                        SourceRelation(
                            current_symbol,
                            imported.group(1),
                            "IMPORTS",
                            "STATIC_CONFIRMED",
                            source.relative_path,
                            number,
                        )
                    )
            declaration = pattern.search(line)
            declaration_name = None
            if declaration:
                groups = [value for value in declaration.groups() if value]
                name = groups[-1]
                declaration_name = name
                symbol_type = groups[0].lower() if len(groups) > 1 else "symbol"
                facts.symbols.append(_symbol(source, name, symbol_type, number, number))
                current_symbol = name
            if source.language not in {"SQL", "Gradle"}:
                for call in self._call.finditer(line):
                    target = call.group(1)
                    if target not in {
                        "if",
                        "for",
                        "while",
                        "switch",
                        "catch",
                        "return",
                        "function",
                    } and target != declaration_name:
                        lowered = target.lower()
                        relation_type = "CALLS"
                        if any(
                            token in lowered
                            for token in ("fetch", "axios", "httpx", "request")
                        ):
                            relation_type = "SENDS_HTTP"
                        elif any(
                            token in lowered
                            for token in ("getenv", "process.env", "config.get")
                        ):
                            relation_type = "READS_CONFIG"
                        elif "retry" in lowered:
                            relation_type = "RETRIES"
                        facts.relations.append(
                            SourceRelation(
                                current_symbol,
                                target,
                                relation_type,
                                "STATIC_CONFIRMED",
                                source.relative_path,
                                number,
                            )
                        )
                if re.search(r"\bthrow\b", line):
                    facts.relations.append(
                        SourceRelation(
                            current_symbol,
                            "throw",
                            "THROWS",
                            "STATIC_CONFIRMED",
                            source.relative_path,
                            number,
                        )
                    )
                if re.search(r"\bcatch\s*\(", line):
                    facts.relations.append(
                        SourceRelation(
                            current_symbol,
                            "catch",
                            "HANDLES_EXCEPTION",
                            "STATIC_CONFIRMED",
                            source.relative_path,
                            number,
                        )
                    )
        return facts


def _flatten_keys(value: object, prefix: str = "") -> list[str]:
    if not isinstance(value, dict):
        return [prefix] if prefix else []
    keys: list[str] = []
    for key, nested in value.items():
        next_prefix = f"{prefix}.{key}" if prefix else str(key)
        keys.extend(_flatten_keys(nested, next_prefix) or [next_prefix])
    return keys


def _strip_json_comments(text: str) -> str:
    result: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(text):
        char = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if in_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            result.append(char)
            index += 1
            continue
        if char == "/" and following == "/":
            index += 2
            while index < len(text) and text[index] not in "\r\n":
                index += 1
            continue
        if char == "/" and following == "*":
            index += 2
            while index + 1 < len(text) and text[index : index + 2] != "*/":
                if text[index] in "\r\n":
                    result.append(text[index])
                index += 1
            index = min(len(text), index + 2)
            continue
        result.append(char)
        index += 1
    return "".join(result)


_JSON_STRING_CONCATENATION = re.compile(
    r'("(?:\\.|[^"\\])*")\s*\+\s*("(?:\\.|[^"\\])*")'
)


def _convert_single_quoted_json_strings(text: str) -> str:
    result: list[str] = []
    index = 0
    in_double_string = False
    escaped = False
    while index < len(text):
        char = text[index]
        if in_double_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_double_string = False
            index += 1
            continue
        if char == '"':
            in_double_string = True
            result.append(char)
            index += 1
            continue
        if char != "'":
            result.append(char)
            index += 1
            continue
        end = index + 1
        escaped = False
        while end < len(text):
            if escaped:
                escaped = False
            elif text[end] == "\\":
                escaped = True
            elif text[end] == "'":
                break
            end += 1
        if end >= len(text):
            result.append(char)
            index += 1
            continue
        literal = text[index : end + 1]
        try:
            value = ast.literal_eval(literal)
        except (SyntaxError, ValueError):
            result.append(literal)
        else:
            result.append(json.dumps(value, ensure_ascii=False))
        index = end + 1
    return "".join(result)


def _remove_json_trailing_commas(text: str) -> str:
    result: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(text):
        char = text[index]
        if in_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
        if char == ",":
            following = index + 1
            while following < len(text) and text[following].isspace():
                following += 1
            if following < len(text) and text[following] in "]}":
                index += 1
                continue
        result.append(char)
        index += 1
    return "".join(result)


def _normalize_legacy_json(text: str) -> str:
    normalized = _convert_single_quoted_json_strings(_strip_json_comments(text))
    while True:
        normalized, replacements = _JSON_STRING_CONCATENATION.subn(
            lambda match: json.dumps(
                json.loads(match.group(1)) + json.loads(match.group(2)),
                ensure_ascii=False,
            ),
            normalized,
        )
        if not replacements:
            return _remove_json_trailing_commas(normalized)


def _parse_xml_with_declared_encoding(path: Path) -> ET.ElementTree:
    try:
        return ET.parse(path)
    except (ET.ParseError, ValueError) as original_error:
        raw = path.read_bytes()
        declared = re.search(
            br"encoding\s*=\s*['\"]([^'\"]+)['\"]",
            raw[:512],
            flags=re.IGNORECASE,
        )
        encodings: list[str] = []
        if declared:
            try:
                encodings.append(declared.group(1).decode("ascii"))
            except UnicodeDecodeError:
                pass
        encodings.extend(["utf-8-sig", "cp949", "euc-kr", "utf-16"])
        text: str | None = None
        for encoding in dict.fromkeys(encodings):
            try:
                text = raw.decode(encoding)
                break
            except (LookupError, UnicodeDecodeError):
                continue
        if text is None:
            raise original_error
        declaration_end = text.find("?>")
        if declaration_end >= 0:
            declaration_end += 2
            text = text[:declaration_end].replace('\\"', '"') + text[declaration_end:]
        text = re.sub(
            r"(<\?xml\b[^>]*?)\s+encoding\s*=\s*(['\"])[^'\"]+\2",
            r"\1",
            text,
            count=1,
            flags=re.IGNORECASE,
        )
        return ET.ElementTree(ET.fromstring(text))


class ConfigurationAnalyzer:
    name = "configuration-keys"

    def supports(self, source: SourceFile) -> bool:
        return source.language in {"YAML", "properties", "JSON", "TOML"}

    def analyze(self, root: Path, source: SourceFile) -> AnalysisFacts:
        facts = AnalysisFacts()
        path = root / source.relative_path
        text = _read_source_text(path)
        try:
            if source.language == "YAML":
                keys = _flatten_keys(yaml.safe_load(text) or {})
            elif source.language == "JSON":
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    payload = json.loads(_normalize_legacy_json(text))
                keys = _flatten_keys(payload)
            elif source.language == "TOML":
                keys = _flatten_keys(tomllib.loads(text))
            else:
                keys = []
                for line in text.splitlines():
                    stripped = line.strip()
                    if stripped and not stripped.startswith(("#", "!")) and "=" in stripped:
                        keys.append(stripped.split("=", 1)[0].strip())
        except (ValueError, yaml.YAMLError) as exc:
            facts.warnings.append(f"{source.relative_path}:config_parse:{type(exc).__name__}")
            return facts
        for key in sorted(set(keys)):
            if not key:
                continue
            line = next(
                (
                    number
                    for number, item in enumerate(text.splitlines(), 1)
                    if key.split(".")[-1] in item
                ),
                None,
            )
            facts.configurations.append(
                ConfigurationReference(
                    key=key,
                    relative_path=source.relative_path,
                    declaration_line=line,
                )
            )
            facts.symbols.append(_symbol(source, key, "configuration key", line or 1, line or 1))
        return facts


class XmlAnalyzer:
    name = "xml-elements"

    def supports(self, source: SourceFile) -> bool:
        return source.language == "XML" and Path(source.relative_path).name != "pom.xml"

    def analyze(self, root: Path, source: SourceFile) -> AnalysisFacts:
        facts = AnalysisFacts()
        try:
            tree = _parse_xml_with_declared_encoding(root / source.relative_path)
        except (ET.ParseError, LookupError, OSError, UnicodeError, ValueError) as exc:
            facts.warnings.append(f"{source.relative_path}:xml_parse:{type(exc).__name__}")
            return facts
        for element in tree.iter():
            local_name = element.tag.rsplit("}", 1)[-1]
            identifier = element.attrib.get("id") or element.attrib.get("name")
            if local_name in {"bean", "route", "endpoint", "queue", "topic"} and identifier:
                facts.symbols.append(_symbol(source, identifier, f"XML {local_name}", 1, 1))
        return facts


class DependencyAnalyzer:
    name = "dependency-manifests"

    def supports(self, source: SourceFile) -> bool:
        return Path(source.relative_path).name in {
            "package.json",
            "pyproject.toml",
            "requirements.txt",
            "pom.xml",
        }

    def analyze(self, root: Path, source: SourceFile) -> AnalysisFacts:
        facts = AnalysisFacts()
        path = root / source.relative_path
        try:
            if path.name == "package.json":
                payload = json.loads(_read_source_text(path))
                for scope in ("dependencies", "devDependencies", "peerDependencies"):
                    for name, version in payload.get(scope, {}).items():
                        facts.dependencies.append(
                            DependencyArtifact(
                                name,
                                str(version),
                                "NPM",
                                "OPEN_SOURCE",
                                source.relative_path,
                                scope,
                            )
                        )
            elif path.name == "pyproject.toml":
                payload = tomllib.loads(_read_source_text(path))
                for item in payload.get("project", {}).get("dependencies", []):
                    name = re.split(r"[<>=!~\[]", item, maxsplit=1)[0].strip()
                    facts.dependencies.append(
                        DependencyArtifact(
                            name,
                            item[len(name) :].strip() or None,
                            "PYPI",
                            "OPEN_SOURCE",
                            source.relative_path,
                        )
                    )
            elif path.name == "requirements.txt":
                for line in _read_source_text(path).splitlines():
                    item = line.strip()
                    if not item or item.startswith(("#", "-")):
                        continue
                    name = re.split(r"[<>=!~\[]", item, maxsplit=1)[0].strip()
                    facts.dependencies.append(
                        DependencyArtifact(
                            name,
                            item[len(name) :].strip() or None,
                            "PYPI",
                            "OPEN_SOURCE",
                            source.relative_path,
                        )
                    )
            else:
                tree = ET.parse(path)
                namespace = {"m": "http://maven.apache.org/POM/4.0.0"}
                for dependency in tree.findall(".//m:dependency", namespace):
                    group = dependency.findtext("m:groupId", default="", namespaces=namespace)
                    artifact = dependency.findtext("m:artifactId", default="", namespaces=namespace)
                    version = dependency.findtext("m:version", default=None, namespaces=namespace)
                    if artifact:
                        facts.dependencies.append(
                            DependencyArtifact(
                                f"{group}:{artifact}".strip(":"),
                                version,
                                "MAVEN",
                                "OPEN_SOURCE",
                                source.relative_path,
                            )
                        )
        except (OSError, ValueError, ET.ParseError) as exc:
            facts.warnings.append(f"{source.relative_path}:dependency_parse:{type(exc).__name__}")
        return facts


DEFAULT_PLUGINS: tuple[AnalyzerPlugin, ...] = (
    PythonAnalyzer(),
    RegexCodeAnalyzer(),
    ConfigurationAnalyzer(),
    XmlAnalyzer(),
    DependencyAnalyzer(),
)


def analyze_files(
    root: Path,
    files: list[SourceFile],
    plugins: tuple[AnalyzerPlugin, ...] = DEFAULT_PLUGINS,
) -> AnalysisFacts:
    facts = AnalysisFacts()
    for source in files:
        for plugin in plugins:
            if not plugin.supports(source):
                continue
            try:
                facts.merge(plugin.analyze(root, source))
            except (OSError, UnicodeError) as exc:
                facts.warnings.append(
                    f"{source.relative_path}:{plugin.name}:{type(exc).__name__}"
                )
    return facts
