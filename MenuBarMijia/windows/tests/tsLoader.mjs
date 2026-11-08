import { existsSync, readFileSync } from "node:fs";
import * as nodeModule from "node:module";
import { fileURLToPath } from "node:url";
import { isMainThread } from "node:worker_threads";
import ts from "typescript";

const EXT_REWRITES = [
  [".js", [".ts", ".tsx"]],
  [".jsx", [".tsx"]],
  [".mjs", [".mts"]],
  [".cjs", [".cts"]],
];

export function resolve(specifier, context, nextResolve) {
  if (specifier.startsWith(".") && context.parentURL) {
    for (const [ext, candidates] of EXT_REWRITES) {
      if (specifier.endsWith(ext)) {
        const stem = specifier.slice(0, -ext.length);
        for (const tsExt of candidates) {
          const tsUrl = new URL(stem + tsExt, context.parentURL);
          if (tsUrl.protocol === "file:" && existsSync(fileURLToPath(tsUrl))) {
            return { url: tsUrl.href, shortCircuit: true };
          }
        }
        break;
      }
    }
  }
  return nextResolve(specifier, context);
}

export function load(url, context, nextLoad) {
  if (url.startsWith("file:")) {
    const parsedUrl = new URL(url);
    const pathname = parsedUrl.pathname;
    if (pathname.endsWith(".json")) {
      const raw = readFileSync(fileURLToPath(parsedUrl), "utf8").replace(/^\uFEFF/, "");
      const parsed = JSON.parse(raw);
      return { format: "module", shortCircuit: true, source: `export default ${JSON.stringify(parsed)};` };
    }
    if (/\.(?:[cm]?ts|tsx)$/.test(pathname)) {
      const fileName = fileURLToPath(parsedUrl);
      const source = readFileSync(fileName, "utf8");
      const { outputText } = ts.transpileModule(source, {
        fileName,
        compilerOptions: {
          target: ts.ScriptTarget.ES2022,
          module: ts.ModuleKind.ESNext,
          jsx: pathname.endsWith(".tsx") ? ts.JsxEmit.ReactJSX : undefined,
          isolatedModules: true,
          inlineSourceMap: true,
        },
      });
      return { format: "module", shortCircuit: true, source: outputText };
    }
  }
  return nextLoad(url, context);
}

if (typeof nodeModule.registerHooks === "function") {
  nodeModule.registerHooks({ resolve, load });
} else if (isMainThread) {
  nodeModule.register("./tsLoader.mjs", import.meta.url);
}
