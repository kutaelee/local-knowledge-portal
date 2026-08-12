export type KnowledgeLocale = "ko" | "en";

const categoryLabels = {
  ko: {
    error_resolution: "오류 해결",
    implementation: "구현 방식",
    custom_success: "검증된 성공 사례",
    performance: "성능·부하",
    operations: "운영·장애",
  },
  en: {
    error_resolution: "Error resolution",
    implementation: "Implementation",
    custom_success: "Validated success",
    performance: "Performance",
    operations: "Operations",
  },
} as const;

export function knowledgeCategoryLabel(value: string, locale: KnowledgeLocale): string {
  const labels = categoryLabels[locale];
  return labels[value as keyof typeof labels] ?? value;
}

export function knowledgeTagLabel(value: string, locale: KnowledgeLocale): string {
  const [namespace, ...rest] = value.split(":");
  const key = rest.join(":");
  if (!key) return value;
  if (namespace === "project") {
    return locale === "ko" ? `프로젝트: ${key}` : `Project: ${key}`;
  }
  if (namespace === "case") {
    return locale === "ko"
      ? `사례: ${knowledgeCategoryLabel(key, locale)}`
      : `Case: ${knowledgeCategoryLabel(key, locale)}`;
  }
  if (namespace === "situation") {
    return locale === "ko"
      ? `작업 특성: ${knowledgeCategoryLabel(key, locale)}`
      : `Work type: ${knowledgeCategoryLabel(key, locale)}`;
  }
  if (namespace === "lifecycle" && key === "verified") {
    return locale === "ko" ? "상태: 검증됨" : "Lifecycle: verified";
  }
  return value;
}
