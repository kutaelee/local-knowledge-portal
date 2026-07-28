"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Boxes,
  CircleCheck,
  Database,
  Power,
  RefreshCcw,
  Server,
  ShieldCheck,
  Sparkles,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";

type Locale = "ko" | "en";
type ManagedComponent = {
  name: string;
  service: string;
  state: string;
  status: string;
};
type ManagedService = {
  id: string;
  label: string;
  category: string;
  description: string;
  state: string;
  detail: string;
  can_start: boolean;
  can_stop: boolean;
  warning: string | null;
  components: ManagedComponent[];
};
type ManagerPayload = {
  status: string;
  checked_at: string;
  services: ManagedService[];
  summary: {
    healthy: number;
    attention: number;
    controllable: number;
  };
};
type ConfirmationPayload = {
  confirmation_token: string;
  expires_in_seconds: number;
};

const copy = {
  ko: {
    eyebrow: "워크스테이션 운영",
    title: "레포·서비스 관리",
    subtitle:
      "등록된 프로젝트, 데이터베이스와 AI 도구의 실제 상태를 확인하고 안전하게 기동·정지합니다.",
    refresh: "상태 새로고침",
    healthy: "정상",
    attention: "확인 필요",
    controllable: "제어 가능",
    checked: "확인 시각",
    readOnly: "조회 전용",
    start: "기동",
    stop: "안전 중지",
    confirmStart: "서비스를 기동할까요?",
    confirmStop: "서비스를 중지할까요?",
    confirm: "실행",
    cancel: "취소",
    closeDialog: "확인 창 닫기",
    preparing: "안전 확인을 준비하고 있습니다…",
    prepareFailed: "확인 절차를 준비하지 못했습니다. 창을 닫고 다시 시도하세요.",
    registeredOnly:
      "이 화면은 사전에 등록된 서비스만 제어합니다. 신규 서비스는 요청 후 운영 설정에 추가합니다.",
    unavailableTitle: "호스트 서비스 관리자에 연결할 수 없습니다",
    unavailableDetail:
      "상태 조회는 계속 유지되지만 기동·정지는 관리자 자동 시작과 API 연결을 확인해야 합니다.",
    empty: "등록된 서비스가 없습니다.",
    categories: {
      projects: "프로젝트",
      ai: "AI 도구",
      infrastructure: "데이터·공유 인프라",
      other: "기타",
    } as Record<string, string>,
    state: {
      healthy: "정상",
      running: "실행 중",
      degraded: "일부만 실행",
      unmanaged: "GPU 큐 외부 실행",
      pending: "기동 대기",
      offline: "중지됨",
      error: "오류",
      unknown: "확인 불가",
    } as Record<string, string>,
  },
  en: {
    eyebrow: "Workstation operations",
    title: "Repositories & services",
    subtitle:
      "Inspect and safely start or stop registered projects, databases, and AI tools.",
    refresh: "Refresh status",
    healthy: "Healthy",
    attention: "Needs attention",
    controllable: "Controllable",
    checked: "Checked",
    readOnly: "Read only",
    start: "Start",
    stop: "Safe stop",
    confirmStart: "Start this service?",
    confirmStop: "Stop this service?",
    confirm: "Confirm",
    cancel: "Cancel",
    closeDialog: "Close confirmation dialog",
    preparing: "Preparing the safety confirmation…",
    prepareFailed: "Could not prepare confirmation. Close this dialog and try again.",
    registeredOnly:
      "Only pre-registered services are controllable. New services are added to operations configuration on request.",
    unavailableTitle: "Host service manager is unavailable",
    unavailableDetail:
      "Read-only health remains available, but start and stop require the manager and API connection.",
    empty: "No services are registered.",
    categories: {
      projects: "Projects",
      ai: "AI tools",
      infrastructure: "Data and shared infrastructure",
      other: "Other",
    } as Record<string, string>,
    state: {
      healthy: "Healthy",
      running: "Running",
      degraded: "Partially running",
      unmanaged: "Running outside GPU queue",
      pending: "Waiting to start",
      offline: "Stopped",
      error: "Error",
      unknown: "Unknown",
    } as Record<string, string>,
  },
};

const knownServiceCopy: Record<
  Locale,
  Record<string, { label: string; description?: string }>
> = {
  ko: {
    "docker:local-knowledge-portal": {
      label: "로컬 지식 포털",
      description: "지식 포털의 웹, API, 수집기, 작업자와 데이터베이스입니다.",
    },
    "docker:gpu-workload-scheduler": {
      label: "GPU 작업 큐 데이터베이스",
      description: "GPU 작업 예약 이력과 실행 상태를 보관하는 데이터베이스입니다.",
    },
    "docker:unjeong-mining-web": { label: "운정 마이닝 웹" },
    "docker:workstation-edge-ingress": { label: "공유 연결 게이트웨이" },
    "docker:local-voice-agent": { label: "로컬 통화비서 데이터베이스" },
    "docker:interstellar-drift": { label: "Interstellar Drift 데이터 서비스" },
    "docker:workstation-databases": { label: "공유 데이터베이스" },
    comfyui: {
      label: "ComfyUI",
      description:
        "화면 서버는 가볍게 유지하고 실제 이미지 생성 요청만 GPU 작업 큐에서 실행합니다.",
    },
    "ai-toolkit": {
      label: "AI-Toolkit",
      description: "WSL에서 실행되는 로컬 모델 학습 인터페이스입니다.",
    },
    ollama: {
      label: "Ollama 공용 모델 서버",
      description: "Hermes와 지식 포털이 함께 사용하는 단일 모델 서버입니다.",
    },
    "gpu-scheduler-host": {
      label: "GPU 작업 스케줄러",
      description: "장시간 GPU 작업의 예약 순서와 안전 여유를 관리합니다.",
    },
  },
  en: {},
};

function displayService(service: ManagedService, locale: Locale) {
  const translated = knownServiceCopy[locale][service.id];
  return {
    label: translated?.label ?? service.label,
    description:
      translated?.description ??
      (locale === "ko" && service.id.startsWith("docker:")
        ? "Docker Compose로 실행되는 등록 서비스 묶음입니다."
        : service.description),
  };
}

function displayWarning(service: ManagedService, locale: Locale) {
  if (locale === "ko" && service.id === "comfyui" && service.warning) {
    return "실행 중이거나 대기 중인 생성 작업이 있으면 안전 중지를 거부합니다.";
  }
  return service.warning;
}

function categoryIcon(category: string) {
  if (category === "ai") return Sparkles;
  if (category === "infrastructure") return Database;
  if (category === "projects") return Boxes;
  return Server;
}

export function ServiceManager({
  locale = "ko",
  embedded = false,
}: {
  locale?: Locale;
  embedded?: boolean;
}) {
  const text = copy[locale];
  const queryClient = useQueryClient();
  const [confirmation, setConfirmation] = useState<{
    service: ManagedService;
    action: "start" | "stop";
    token: string | null;
  } | null>(null);
  const services = useQuery({
    queryKey: ["service-manager"],
    queryFn: () => api<ManagerPayload>("/api/v1/service-manager"),
    refetchInterval: 10_000,
  });
  const prepareMutation = useMutation({
    mutationFn: ({
      service,
      action,
    }: {
      service: ManagedService;
      action: "start" | "stop";
    }) =>
      api<ConfirmationPayload>(
        `/api/v1/service-manager/${encodeURIComponent(service.id)}/${action}/confirmation`,
        { method: "POST" }
      ),
    onSuccess: (result, variables) => {
      setConfirmation((current) =>
        current?.service.id === variables.service.id &&
        current.action === variables.action
          ? { ...current, token: result.confirmation_token }
          : current
      );
    },
  });
  const mutation = useMutation({
    mutationFn: ({
      service,
      action,
      token,
    }: {
      service: ManagedService;
      action: "start" | "stop";
      token: string;
    }) =>
      api(`/api/v1/service-manager/${encodeURIComponent(service.id)}/${action}`, {
        method: "POST",
        body: JSON.stringify({
          confirmed: true,
          confirmation_token: token,
        }),
      }),
    onSuccess: async () => {
      setConfirmation(null);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["service-manager"] }),
        queryClient.invalidateQueries({ queryKey: ["system-services"] }),
      ]);
    },
  });
  const resetPreparation = prepareMutation.reset;
  const resetControl = mutation.reset;
  const requestConfirmation = (
    service: ManagedService,
    action: "start" | "stop"
  ) => {
    resetControl();
    resetPreparation();
    setConfirmation({ service, action, token: null });
    prepareMutation.mutate({ service, action });
  };
  const closeConfirmation = useCallback(() => {
    if (mutation.isPending) return;
    setConfirmation(null);
    resetPreparation();
    resetControl();
  }, [mutation.isPending, resetControl, resetPreparation]);
  const grouped = useMemo(() => {
    const result = new Map<string, ManagedService[]>();
    for (const service of services.data?.services ?? []) {
      const category = service.category || "other";
      result.set(category, [...(result.get(category) ?? []), service]);
    }
    return ["projects", "ai", "infrastructure", "other"]
      .map((category) => ({
        category,
        services: (result.get(category) ?? []).sort((a, b) =>
          displayService(a, locale).label.localeCompare(
            displayService(b, locale).label,
            locale === "ko" ? "ko-KR" : "en-US"
          )
        ),
      }))
      .filter((group) => group.services.length);
  }, [locale, services.data?.services]);
  useEffect(() => {
    if (!confirmation) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !mutation.isPending) {
        closeConfirmation();
      }
    };
    addEventListener("keydown", closeOnEscape);
    return () => removeEventListener("keydown", closeOnEscape);
  }, [closeConfirmation, confirmation, mutation.isPending]);
  const confirmationWarning = confirmation
    ? displayWarning(confirmation.service, locale)
    : null;

  return (
    <section className={`service-manager-page ${embedded ? "embedded" : ""}`}>
      <header className="page-header service-manager-header">
        <div>
          <p className="eyebrow">{text.eyebrow}</p>
          <h1>{text.title}</h1>
          <p>{text.subtitle}</p>
        </div>
        <button
          type="button"
          className="secondary-button"
          onClick={() => services.refetch()}
          disabled={services.isFetching}
        >
          <RefreshCcw size={15} /> {text.refresh}
        </button>
      </header>

      {services.isError ? (
        <div className="service-manager-error" role="alert">
          <AlertTriangle size={20} />
          <div>
            <strong>{text.unavailableTitle}</strong>
            <p>{text.unavailableDetail}</p>
          </div>
        </div>
      ) : (
        <>
          <div className="service-summary-grid" aria-label={text.title}>
            <article>
              <CircleCheck size={18} />
              <span>{text.healthy}</span>
              <strong>{services.data?.summary.healthy ?? "—"}</strong>
            </article>
            <article>
              <AlertTriangle size={18} />
              <span>{text.attention}</span>
              <strong>{services.data?.summary.attention ?? "—"}</strong>
            </article>
            <article>
              <Power size={18} />
              <span>{text.controllable}</span>
              <strong>{services.data?.summary.controllable ?? "—"}</strong>
            </article>
          </div>

          <div className="service-policy-note">
            <ShieldCheck size={17} />
            <span>{text.registeredOnly}</span>
            {services.data?.checked_at && (
              <time dateTime={services.data.checked_at}>
                {text.checked}:{" "}
                {new Date(services.data.checked_at).toLocaleTimeString(
                  locale === "ko" ? "ko-KR" : "en-US"
                )}
              </time>
            )}
          </div>

          {grouped.length ? (
            <div className="service-sections">
              {grouped.map((group) => {
                const Icon = categoryIcon(group.category);
                return (
                  <section key={group.category}>
                    <h2>
                      <Icon size={18} />
                      {text.categories[group.category] ?? group.category}
                    </h2>
                    <div className="managed-service-grid">
                      {group.services.map((service) => {
                        const display = displayService(service, locale);
                        return (
                        <article className="managed-service-card" key={service.id}>
                          <div className="managed-service-title">
                            <div>
                              <h3>{display.label}</h3>
                              <span
                                className={`service-state state-${service.state}`}
                              >
                                {text.state[service.state] ?? service.state}
                              </span>
                            </div>
                            <p>{display.description}</p>
                          </div>
                          <div className="managed-service-detail">
                            <strong>{service.detail}</strong>
                            {service.components.length > 0 && (
                              <ul>
                                {service.components.slice(0, 6).map((component) => (
                                  <li key={`${component.name}:${component.service}`}>
                                    <span>{component.service || component.name}</span>
                                    <small>{component.status || component.state}</small>
                                  </li>
                                ))}
                              </ul>
                            )}
                          </div>
                          <div className="managed-service-actions">
                            {service.can_start && (
                              <button
                                type="button"
                                className="primary-button"
                                onClick={() => requestConfirmation(service, "start")}
                              >
                                <Power size={15} /> {text.start}
                              </button>
                            )}
                            {service.can_stop && (
                              <button
                                type="button"
                                className="danger-button"
                                onClick={() => requestConfirmation(service, "stop")}
                              >
                                <Power size={15} /> {text.stop}
                              </button>
                            )}
                            {!service.can_start && !service.can_stop && (
                              <span className="read-only-label">
                                <ShieldCheck size={14} /> {text.readOnly}
                              </span>
                            )}
                          </div>
                        </article>
                        );
                      })}
                    </div>
                  </section>
                );
              })}
            </div>
          ) : (
            <div className="empty-card">{text.empty}</div>
          )}
        </>
      )}

      {confirmation && (
        <div className="confirm-backdrop" role="presentation">
          <section
            className="service-confirm-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="service-confirm-title"
          >
            <button
              type="button"
              className="dialog-close"
              onClick={closeConfirmation}
              aria-label={text.closeDialog}
            >
              <X size={18} />
            </button>
            <Power size={24} />
            <h2 id="service-confirm-title">
              {confirmation.action === "start"
                ? text.confirmStart
                : text.confirmStop}
            </h2>
            <strong>{displayService(confirmation.service, locale).label}</strong>
            {confirmationWarning && <p>{confirmationWarning}</p>}
            {prepareMutation.isPending && <p>{text.preparing}</p>}
            {prepareMutation.isError && (
              <p className="action-error">{text.prepareFailed}</p>
            )}
            {mutation.isError && (
              <p className="action-error">
                {locale === "ko"
                  ? "요청이 거부되었습니다. 실행 중 작업과 서비스 상태를 확인하세요."
                  : "The request was rejected. Check active work and service state."}
              </p>
            )}
            <div>
              <button
                type="button"
                className="secondary-button"
                onClick={closeConfirmation}
                autoFocus
              >
                {text.cancel}
              </button>
              <button
                type="button"
                className={
                  confirmation.action === "stop"
                    ? "danger-button"
                    : "primary-button"
                }
                disabled={
                  mutation.isPending ||
                  prepareMutation.isPending ||
                  !confirmation.token
                }
                onClick={() => {
                  if (!confirmation.token) return;
                  mutation.mutate({
                    service: confirmation.service,
                    action: confirmation.action,
                    token: confirmation.token,
                  });
                }}
              >
                {text.confirm}
              </button>
            </div>
          </section>
        </div>
      )}
    </section>
  );
}
