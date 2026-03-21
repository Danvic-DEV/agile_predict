const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "/api/v1";

export class ApiError extends Error {
  status: number;
  code: string | null;

  constructor(message: string, status: number, code: string | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

async function buildApiError(response: Response): Promise<ApiError> {
  let message = `Request failed with status ${response.status}`;
  let code: string | null = null;

  try {
    const payload = (await response.json()) as {
      detail?: { code?: string; message?: string; error?: string } | string;
    };
    if (typeof payload.detail === "string") {
      message = payload.detail;
    } else if (payload.detail && typeof payload.detail === "object") {
      code = typeof payload.detail.code === "string" ? payload.detail.code : null;
      if (typeof payload.detail.message === "string" && payload.detail.message.length > 0) {
        message = payload.detail.message;
      }
    }
  } catch {
    // Fall back to a generic error when the response body is not JSON.
  }

  return new ApiError(message, response.status, code);
}

export async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`);
  if (!response.ok) {
    throw await buildApiError(response);
  }
  return (await response.json()) as T;
}

export async function postJson<TResponse, TBody>(path: string, body: TBody): Promise<TResponse> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw await buildApiError(response);
  }
  return (await response.json()) as TResponse;
}

export function getApiBaseUrl(): string {
  return API_BASE_URL;
}
