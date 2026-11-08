import { Action, ActionPanel, Form, Icon } from "@raycast/api";
import { runCommandWithToast } from "./url";

export default function Ask() {
  return (
    <Form
      actions={
        <ActionPanel>
          <Action.SubmitForm
            title="Send Command"
            icon={Icon.ArrowRight}
            onSubmit={(values: { text?: string }) => {
              const text = (values.text ?? "").trim();
              if (text.length === 0) {
                return;
              }
              return runCommandWithToast("ask", [["text", text]]);
            }}
          />
        </ActionPanel>
      }
    >
      <Form.TextArea
        id="text"
        title="Command"
        placeholder="Turn the living room light to 40%"
        info="Runs through MenuBarMijia, so the same local parsing, high-risk confirmation and lock-screen rules apply."
        autoFocus
      />
      <Form.Description text="Examples: 把客厅灯调到 40% · 打开书房台灯 · 空调调到 26 度 · 关闭全屋灯光" />
    </Form>
  );
}
