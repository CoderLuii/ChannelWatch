import fs from "node:fs"
import path from "node:path"
import ts from "typescript"
import { describe, expect, it } from "vitest"

type Contract = {
  method: string
  path: string
  auth: "middleware" | "public" | "session" | "session-csrf" | "setup-state"
  minimum_role: "operator" | "admin" | null
  legacy_alias: boolean
  error_shape: "structured-detail"
  client: string
  client_marker: string
}

type BrowserCall = {
  method: string
  path: string
  source: string
}

const uiRoot = path.resolve(import.meta.dirname, "..")
const contracts = JSON.parse(
  fs.readFileSync(path.join(uiRoot, "api-contracts.json"), "utf8"),
) as Contract[]

function productionTypeScriptFiles(): string[] {
  const files: string[] = []
  const visit = (current: string) => {
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const absolute = path.join(current, entry.name)
      if (entry.isDirectory()) visit(absolute)
      else if (/\.(?:ts|tsx)$/u.test(entry.name) && !entry.name.includes(".test.")) {
        files.push(absolute)
      }
    }
  }
  for (const directory of ["app", "components", "lib"]) {
    const absolute = path.join(uiRoot, directory)
    if (fs.existsSync(absolute)) visit(absolute)
  }
  return files.sort()
}

function routeParameter(expression: ts.Expression): string | null {
  let candidate = expression
  if (
    ts.isCallExpression(candidate)
    && ts.isIdentifier(candidate.expression)
    && candidate.expression.text === "encodeURIComponent"
    && candidate.arguments.length === 1
  ) {
    candidate = candidate.arguments[0]
  }
  if (!ts.isIdentifier(candidate)) return null
  const snakeCase = candidate.text.replace(/[A-Z]/gu, (letter) => `_${letter.toLowerCase()}`)
  return snakeCase === "url_test_name" ? "test_name_url" : snakeCase
}

function isQuerySuffix(expression: ts.Expression): boolean {
  if (!ts.isConditionalExpression(expression)) return false
  return [expression.whenTrue, expression.whenFalse].every((branch) => {
    if (ts.isStringLiteralLike(branch)) return branch.text === "" || branch.text.startsWith("?")
    return ts.isTemplateExpression(branch) && branch.head.text.startsWith("?")
  })
}

function variableInitializers(sourceFile: ts.SourceFile): Map<string, ts.Expression> {
  const initializers = new Map<string, ts.Expression>()
  const visit = (node: ts.Node) => {
    if (
      ts.isVariableDeclaration(node)
      && ts.isIdentifier(node.name)
      && node.initializer
    ) {
      initializers.set(node.name.text, node.initializer)
    }
    ts.forEachChild(node, visit)
  }
  visit(sourceFile)
  return initializers
}

function staticUrlText(
  expression: ts.Expression,
  initializers: Map<string, ts.Expression>,
  resolving: Set<string> = new Set(),
): string | null {
  if (ts.isStringLiteralLike(expression)) return expression.text
  if (ts.isIdentifier(expression) && expression.text === "API_BASE") return "/api"
  if (ts.isIdentifier(expression) && initializers.has(expression.text)) {
    if (resolving.has(expression.text)) return null
    const nextResolving = new Set(resolving).add(expression.text)
    const resolved = staticUrlText(initializers.get(expression.text)!, initializers, nextResolving)
    if (resolved !== null) return resolved
  }
  if (
    ts.isBinaryExpression(expression)
    && expression.operatorToken.kind === ts.SyntaxKind.PlusToken
  ) {
    const left = staticUrlText(expression.left, initializers, resolving)
    const right = staticUrlText(expression.right, initializers, resolving)
    return left === null || right === null ? null : left + right
  }
  if (!ts.isTemplateExpression(expression)) return null

  let rendered = expression.head.text
  for (const span of expression.templateSpans) {
    if (!rendered.includes("?")) {
      const staticPart = staticUrlText(span.expression, initializers, resolving)
      const parameter = routeParameter(span.expression)
      if (staticPart !== null) rendered += staticPart
      else if (parameter) rendered += `{${parameter}}`
      else if (!isQuerySuffix(span.expression)) return null
    }
    rendered += span.literal.text
  }

  return rendered
}

function browserApiPath(
  expression: ts.Expression,
  initializers: Map<string, ts.Expression>,
): string | null {
  const rendered = staticUrlText(expression, initializers)
  if (rendered === null) return null

  return rendered.startsWith("/api/") ? rendered.split("?", 1)[0] : null
}

function requestMethod(call: ts.CallExpression): string {
  const options = call.arguments[1]
  if (!options || !ts.isObjectLiteralExpression(options)) return "GET"
  for (const property of options.properties) {
    if (
      ts.isPropertyAssignment(property)
      && property.name.getText() === "method"
      && ts.isStringLiteralLike(property.initializer)
    ) {
      return property.initializer.text.toUpperCase()
    }
  }
  return "GET"
}

function discoverDirectBrowserCalls(): BrowserCall[] {
  const calls: BrowserCall[] = []
  for (const absolute of productionTypeScriptFiles()) {
    const sourceFile = ts.createSourceFile(
      absolute,
      fs.readFileSync(absolute, "utf8"),
      ts.ScriptTarget.Latest,
      true,
      absolute.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
    )
    const initializers = variableInitializers(sourceFile)
    const visit = (node: ts.Node) => {
      if (
        ts.isCallExpression(node)
        && ts.isIdentifier(node.expression)
        && node.expression.text === "fetch"
        && node.arguments[0]
      ) {
        const apiPath = browserApiPath(node.arguments[0], initializers)
        if (apiPath) {
          calls.push({
            method: requestMethod(node),
            path: apiPath,
            source: path.relative(uiRoot, absolute),
          })
        }
      }
      ts.forEachChild(node, visit)
    }
    visit(sourceFile)
  }
  return calls
}

function discoverIndirectSameOriginCalls(): BrowserCall[] {
  const calls: BrowserCall[] = []
  for (const absolute of productionTypeScriptFiles()) {
    const sourceFile = ts.createSourceFile(
      absolute,
      fs.readFileSync(absolute, "utf8"),
      ts.ScriptTarget.Latest,
      true,
      absolute.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
    )
    const visit = (node: ts.Node) => {
      if (
        ts.isCallExpression(node)
        && ts.isIdentifier(node.expression)
        && node.expression.text === "submitReport"
        && node.arguments[0]
      ) {
        const visitEndpoint = (candidate: ts.Node) => {
          if (ts.isStringLiteralLike(candidate) && candidate.text.startsWith("/api/")) {
            calls.push({
              method: "POST",
              path: candidate.text.split("?", 1)[0],
              source: path.relative(uiRoot, absolute),
            })
          }
          ts.forEachChild(candidate, visitEndpoint)
        }
        visitEndpoint(node.arguments[0])
      }
      ts.forEachChild(node, visit)
    }
    visit(sourceFile)
  }
  return calls
}

function discoverDynamicBrowserFetches(): string[] {
  const fetches: string[] = []
  for (const absolute of productionTypeScriptFiles()) {
    const sourceFile = ts.createSourceFile(
      absolute,
      fs.readFileSync(absolute, "utf8"),
      ts.ScriptTarget.Latest,
      true,
      absolute.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
    )
    const initializers = variableInitializers(sourceFile)
    const visit = (node: ts.Node) => {
      if (
        ts.isCallExpression(node)
        && ts.isIdentifier(node.expression)
        && node.expression.text === "fetch"
        && node.arguments[0]
        && !browserApiPath(node.arguments[0], initializers)
      ) {
        fetches.push(
          `${path.relative(uiRoot, absolute)}:${node.arguments[0].getText(sourceFile)}`,
        )
      }
      ts.forEachChild(node, visit)
    }
    visit(sourceFile)
  }
  return fetches.sort()
}

function discoverSameOriginApiLiterals(): Set<string> {
  const paths = new Set<string>()
  for (const absolute of productionTypeScriptFiles()) {
    const sourceFile = ts.createSourceFile(
      absolute,
      fs.readFileSync(absolute, "utf8"),
      ts.ScriptTarget.Latest,
      true,
      absolute.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
    )
    const visit = (node: ts.Node) => {
      if (ts.isStringLiteralLike(node) && node.text.startsWith("/api/")) {
        let ancestor: ts.Node | undefined = node.parent
        let externalUrlComponent = false
        while (ancestor && ancestor !== sourceFile) {
          if (
            ts.isNewExpression(ancestor)
            && ts.isIdentifier(ancestor.expression)
            && ancestor.expression.text === "URL"
          ) {
            externalUrlComponent = true
            break
          }
          if (ts.isCallExpression(ancestor)) break
          ancestor = ancestor.parent
        }
        if (!externalUrlComponent) paths.add(node.text.split("?", 1)[0])
      }
      ts.forEachChild(node, visit)
    }
    visit(sourceFile)
  }
  return paths
}

describe("browser API contract manifest", () => {
  it("bidirectionally inventories direct browser fetch calls", () => {
    const directCalls = discoverDirectBrowserCalls()
    const indirectCalls = discoverIndirectSameOriginCalls()
    const directIdentities = directCalls.map(({ method, path: apiPath }) => `${method} ${apiPath}`)
    const indirectIdentities = indirectCalls.map(
      ({ method, path: apiPath }) => `${method} ${apiPath}`,
    )
    const contractIdentities = new Set(
      contracts.map(({ method, path: apiPath }) => `${method} ${apiPath}`),
    )

    expect(directCalls.length).toBeGreaterThan(0)
    expect(new Set(directIdentities).size).toBe(directIdentities.length)
    expect(indirectCalls).toEqual([
      {
        method: "POST",
        path: "/api/v1/support/report-dry-run",
        source: "components/feature-request-dialog.tsx",
      },
      {
        method: "POST",
        path: "/api/v1/support/report-dry-run",
        source: "components/report-problem-dialog.tsx",
      },
    ])
    for (const call of directCalls) {
      expect(contractIdentities.has(`${call.method} ${call.path}`), call.source).toBe(true)
    }

    const discoverableContractIdentities = new Set([
      ...directIdentities,
      ...indirectIdentities,
    ])
    for (const contract of contracts) {
      if (contract.client.startsWith("playwright/")) {
        discoverableContractIdentities.add(`${contract.method} ${contract.path}`)
      }
    }
    expect([...contractIdentities].sort()).toEqual([...discoverableContractIdentities].sort())
  })

  it("resolves API_BASE templates assigned before fetch with their method", () => {
    const sourceFile = ts.createSourceFile(
      "assigned.ts",
      "const route = `${API_BASE}/v1/update/jobs/${encodeURIComponent(jobId)}`; fetch(route, { method: 'POST' })",
      ts.ScriptTarget.Latest,
      true,
      ts.ScriptKind.TS,
    )
    let discovered: BrowserCall | null = null
    const initializers = variableInitializers(sourceFile)
    const visit = (node: ts.Node) => {
      if (
        ts.isCallExpression(node)
        && ts.isIdentifier(node.expression)
        && node.expression.text === "fetch"
      ) {
        discovered = {
          method: requestMethod(node),
          path: browserApiPath(node.arguments[0], initializers)!,
          source: "assigned.ts",
        }
      }
      ts.forEachChild(node, visit)
    }
    visit(sourceFile)

    expect(discovered).toEqual({
      method: "POST",
      path: "/api/v1/update/jobs/{job_id}",
      source: "assigned.ts",
    })
  })

  it("tracks indirect same-origin endpoints and every declared call site", () => {
    const contractPaths = new Set(contracts.map((contract) => contract.path))
    for (const apiPath of discoverSameOriginApiLiterals()) {
      expect(contractPaths.has(apiPath), apiPath).toBe(true)
    }
    for (const contract of contracts) {
      const clientSource = fs.readFileSync(path.join(uiRoot, contract.client), "utf8")
      expect(clientSource, `${contract.method} ${contract.path}`).toContain(
        contract.client_marker,
      )
    }
  })

  it("requires an explicit disposition for every dynamic browser fetch", () => {
    expect(discoverDynamicBrowserFetches()).toEqual([
      "lib/api.ts:`/healthz/live`",
      "lib/api.ts:`/healthz/ready`",
      "lib/api.ts:`/healthz/startup`",
      "lib/api.ts:challengeUrl",
      "lib/api.ts:endpoint",
      "lib/api.ts:statusEndpoint",
    ])
  })

  it("has a complete, unique policy record for every endpoint", () => {
    const identities = contracts.map(({ method, path: apiPath }) => `${method} ${apiPath}`)
    expect(new Set(identities).size).toBe(identities.length)
    for (const contract of contracts) {
      expect(["middleware", "public", "session", "session-csrf", "setup-state"]).toContain(
        contract.auth,
      )
      expect([null, "operator", "admin"]).toContain(contract.minimum_role)
      expect(typeof contract.legacy_alias).toBe("boolean")
      expect(contract.error_shape).toBe("structured-detail")
    }
  })
})
