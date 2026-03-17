const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "/api/v1";

export async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`);
  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
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
    const detail = await response.text();
    throw new Error(`Request failed with status ${response.status}: ${detail}`);
  }
  return (await response.json()) as TResponse;
}

export function getApiBaseUrl(): string {
  return API_BASE_URL;
}
