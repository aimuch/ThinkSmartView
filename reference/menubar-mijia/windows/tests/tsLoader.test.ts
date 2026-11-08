import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, it } from "node:test";
import { pathToFileURL } from "node:url";
import { load, resolve } from "./tsLoader.mjs";

describe("tsLoader", () => {
  it("rewrites relative .js specifiers to sibling .ts files when present", async () => {
    const dir = mkdtempSync(join(tmpdir(), "tsloader-test-"));
    try {
      const tsFile = join(dir, "sample.ts");
      writeFileSync(tsFile, "export const x: number = 1;\n", "utf8");
      const parentURL = pathToFileURL(join(dir, "caller.ts")).href;

      const resolved = await resolve("./sample.js", { parentURL }, async () => {
        throw new Error("should not call nextResolve when sibling .ts exists");
      });
      assert.equal(resolved.shortCircuit, true);
      assert.equal(resolved.url, pathToFileURL(tsFile).href);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it("delegates to nextResolve when sibling .ts does not exist or specifier is bare", async () => {
    const dir = mkdtempSync(join(tmpdir(), "tsloader-test-"));
    try {
      const parentURL = pathToFileURL(join(dir, "caller.ts")).href;
      let fallbackCalls = 0;
      const nextResolve = async (spec: string) => {
        fallbackCalls += 1;
        return { url: `fallback:${spec}`, shortCircuit: true };
      };

      const missing = await resolve("./missing.js", { parentURL }, nextResolve);
      assert.equal(missing.url, "fallback:./missing.js");

      const bare = await resolve("node:fs", { parentURL }, nextResolve);
      assert.equal(bare.url, "fallback:node:fs");
      assert.equal(fallbackCalls, 2);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it("transpiles TypeScript files including parameter properties and loads JSON as ESM default export", async () => {
    const dir = mkdtempSync(join(tmpdir(), "tsloader-test-"));
    try {
      const tsFile = join(dir, "widget.ts");
      writeFileSync(
        tsFile,
        "export class Box { constructor(public readonly value: number = 42) {} }\n",
        "utf8",
      );
      const tsResult = await load(pathToFileURL(tsFile).href, {}, async () => {
        throw new Error("should not call nextLoad for .ts");
      });
      assert.equal(tsResult.format, "module");
      assert.equal(tsResult.shortCircuit, true);
      assert.match(String(tsResult.source), /this\.value = value/);

      const jsonFile = join(dir, "data.json");
      writeFileSync(jsonFile, '\uFEFF{"hello":"world"}', "utf8");
      const jsonResult = await load(pathToFileURL(jsonFile).href, {}, async () => {
        throw new Error("should not call nextLoad for .json");
      });
      assert.equal(jsonResult.format, "module");
      assert.equal(jsonResult.shortCircuit, true);
      assert.equal(jsonResult.source, 'export default {"hello":"world"};');

      const badJsonFile = join(dir, "bad.json");
      writeFileSync(badJsonFile, "not valid json", "utf8");
      await assert.rejects(async () => {
        await load(pathToFileURL(badJsonFile).href, {}, async () => ({
          format: "module",
          shortCircuit: true,
          source: "",
        }));
      }, SyntaxError);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it("supports .mts/.tsx specifiers, query strings, and delegates non-file URLs to nextLoad", async () => {
    const dir = mkdtempSync(join(tmpdir(), "tsloader-test-"));
    try {
      const mtsFile = join(dir, "mod.mts");
      writeFileSync(mtsFile, "export const v: string = 'ok';\n", "utf8");
      const parentURL = pathToFileURL(join(dir, "caller.mts")).href;

      const resolvedMts = await resolve("./mod.mjs", { parentURL }, async () => {
        throw new Error("should not call nextResolve for .mjs -> .mts");
      });
      assert.equal(resolvedMts.url, pathToFileURL(mtsFile).href);

      const loadedWithQuery = await load(`${pathToFileURL(mtsFile).href}?v=1`, {}, async () => {
        throw new Error("should not call nextLoad for .mts?v=1");
      });
      assert.equal(loadedWithQuery.format, "module");
      assert.match(String(loadedWithQuery.source), /export const v = 'ok'/);

      let passthroughCalled = false;
      const nonFile = await load("https://example.invalid/remote.json", {}, async (u: string) => {
        passthroughCalled = true;
        return { format: "module", shortCircuit: true, source: `// ${u}` };
      });
      assert.equal(passthroughCalled, true);
      assert.equal(nonFile.source, "// https://example.invalid/remote.json");
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});
