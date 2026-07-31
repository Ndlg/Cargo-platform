import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const html = fs.readFileSync(
  new URL("../public/ai-recognition-console.html", import.meta.url),
  "utf8",
);
const script = html.match(/<script>([\s\S]*?)<\/script>/)?.[1];
assert.ok(script, "console script must exist");

const button = {
  disabled: false,
  isConnected: true,
  textContent: "确认并同步规则",
};
const status = { textContent: "" };
const error = { textContent: "旧错误" };
const values = {
  product: "",
  sales_attr1: "",
  sales_attr2: "",
  quantity: "1",
  remark: "",
};
const row = {
  querySelector(selector) {
    const field = selector.match(/data-field="([^"]+)"/)?.[1];
    return { value: values[field] };
  },
};
const table = {
  querySelectorAll(selector) {
    assert.equal(selector, "tr");
    return [row];
  },
};
let alertCount = 0;
let disconnectOnAlert = false;
const elements = {
  "#administrator-result": table,
  "#confirm-button": button,
  "#operation-status": status,
  "#operation-error": error,
  "#feedback-note": { value: "" },
  "#session": { innerHTML: "" },
};
const context = {
  URLSearchParams,
  clearTimeout() {},
  setTimeout() {
    return 1;
  },
  location: {
    search: "?session=session-1",
    origin: "http://cargo.test",
  },
  localStorage: {
    getItem() {
      return "test";
    },
  },
  document: {
    querySelector(selector) {
      return elements[selector];
    },
  },
  fetch() {
    return new Promise(() => {});
  },
};
context.window = {
  alert() {
    alertCount += 1;
    if (disconnectOnAlert) {
      button.isConnected = false;
      button.textContent = "detached";
    }
  },
  parent: { postMessage() {} },
};
vm.createContext(context);
vm.runInContext(script, context);

await vm.runInContext("confirmAndSync()", context);
await vm.runInContext("confirmAndSync()", context);

assert.equal(alertCount, 2, "validation failure must remain retryable");
assert.equal(button.disabled, false);
assert.equal(button.textContent, "确认并同步规则");
assert.equal(error.textContent, "", "a new attempt must clear the previous error");

disconnectOnAlert = true;
await vm.runInContext("confirmAndSync()", context);

assert.equal(button.textContent, "detached", "a detached button must not be rewritten");
