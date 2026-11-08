import { open, showToast, Toast } from "@raycast/api";
import { readFile } from "node:fs/promises";
import { loadSnapshot, resultTimeoutSeconds, snapshotPath, type RaycastFeedback } from "./snapshot";

export type CommandParams = Array<[string, string]>;

export interface CommandResult {
  requestId: string;
  message: string;
  isError: boolean;
}

/**
 * `menumijia://<route>?…` with the local bridge token and a request id. `encodeURIComponent` writes
 * spaces as %20; the app reads a literal "+" as part of the text, so never use `URLSearchParams`.
 */
export function buildCommandURL(
  route: string,
  params: CommandParams,
  token: string,
  requestId: string,
): string {
  const query = [...params, ["id", requestId], ["token", token]] as CommandParams;
  const encoded = query
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(value)}`)
    .join("&");
  return `menumijia://${route}?${encoded}`;
}

export async function runCommand(
  route: string,
  params: CommandParams = [],
  options: { wait?: boolean } = {},
): Promise<CommandResult> {
  const snapshot = await loadSnapshot();
  const token = (snapshot.bridgeToken ?? "").trim();
  if (!token) {
    throw new Error(
      "The snapshot has no bridge token. Toggle Raycast 集成 off and on again in MenuBarMijia.",
    );
  }
  const requestId = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  await open(buildCommandURL(route, params, token, requestId));
  if (options.wait === false) {
    return { requestId, message: "", isError: false };
  }
  const feedback = await waitForFeedback(requestId);
  return { requestId, message: feedback.message, isError: feedback.isError };
}

export async function runCommandWithToast(route: string, params: CommandParams = []): Promise<void> {
  try {
    const result = await runCommand(route, params);
    await showToast({
      style: result.isError ? Toast.Style.Failure : Toast.Style.Success,
      title: result.message || (result.isError ? "The command failed" : "Done"),
    });
  } catch (error) {
    await showToast({ style: Toast.Style.Failure, title: "MenuBarMijia", message: errorMessage(error) });
  }
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

async function waitForFeedback(requestId: string): Promise<RaycastFeedback> {
  const path = snapshotPath();
  const deadline = Date.now() + resultTimeoutSeconds() * 1000;
  while (Date.now() < deadline) {
    const { promise, resolve } = Promise.withResolvers<void>();
    setTimeout(resolve, 150);
    await promise;
    try {
      const parsed = JSON.parse(await readFile(path, "utf8")) as { lastCommandFeedback?: RaycastFeedback };
      const feedback = parsed.lastCommandFeedback;
      if (feedback?.requestId === requestId) {
        return feedback;
      }
    } catch {
      // A read that fails mid-write or a file that has no feedback yet only means "not yet".
    }
  }
  throw new Error(
    "MenuBarMijia did not report a result in time. Is the app running, and is Raycast 集成 still on?",
  );
}
