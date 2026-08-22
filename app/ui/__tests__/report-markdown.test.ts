import { describe, expect, it } from "vitest"

import {
  escapeMarkdownTableCell,
  publicDiagnosticsRows,
  redactPublicText,
} from "@/lib/report-markdown"

describe("report markdown helpers", () => {
  it("escapes table-breaking values without dropping readable text", () => {
    const value = "online | healthy\\path\r\nnext\tline\u0007tail"

    expect(escapeMarkdownTableCell(value)).toBe("online \\| healthy\\\\path\\nnext line tail")
  })

  it("matches Worker privacy redaction in the browser-visible preview", () => {
    const redacted = redactPublicText(
      "Local http://[fd00::1234]:8089, fe80::beef, fe80::1%eth0, and fd12::1%5 failed; " +
        "private fe80::1. fd00::1! fe80::1/64? 'fd00::2'; " +
        "host=fd00::1 host:fd00::2 address/fd00::3; " +
        "expanded 0:0:0:0:0:0:0:1 and 0000:0000:0000:0000:0000:0000:0000:0001; " +
        "mapped ::ffff:c0a8:101 ::ffff:7f00:1 ::ffff:192.168.1.1 ::ffff:010.0.0.1 ::ffff:0010.0.0.1 " +
        "0:0:0:0:0:ffff:c0a8:101; punctuation fd00::4... fd00::5: fd00::6. " +
        "public 2001:db8::1 remains; download https://files.example.com/report?" +
        "X-Amz-Signature=secret&AWSAccessKeyId=AKIAEXAMPLE&GoogleAccessId=gcs-signer&" +
        "sig=compact-secret&safe=value. " +
        "Pixel ![tracker][pixel] and shortcut ![beacon]\n\n" +
        "[pixel]: https://evil.example/pixel\n[beacon]: https://evil.example/beacon",
    )

    expect(redacted).toContain("2001:db8::1")
    expect(redacted).toContain("safe=value")
    expect(redacted).toContain("X-Amz-Signature=[redacted]")
    expect(redacted).toContain("AWSAccessKeyId=[redacted]")
    expect(redacted).toContain("GoogleAccessId=[redacted]")
    expect(redacted).toContain("sig=[redacted]")
    for (const privateValue of [
      "fd00::1234",
      "fe80::beef",
      "fe80::1%eth0",
      "fd12::1%5",
      "fe80::1.",
      "fd00::1!",
      "fe80::1/64",
      "fd00::2",
      "host=fd00::1",
      "host:fd00::2",
      "address/fd00::3",
      "0:0:0:0:0:0:0:1",
      "0000:0000:0000:0000:0000:0000:0000:0001",
      "::ffff:c0a8:101",
      "::ffff:7f00:1",
      "::ffff:192.168.1.1",
      "::ffff:010.0.0.1",
      "::ffff:0010.0.0.1",
      "0:0:0:0:0:ffff:c0a8:101",
      "fd00::4",
      "fd00::5",
      "fd00::6",
      "secret",
      "AKIAEXAMPLE",
      "gcs-signer",
      "compact-secret",
      "evil.example",
      "![",
      "[pixel]:",
      "[beacon]:",
    ]) {
      expect(redacted).not.toContain(privateValue)
    }
    expect(redactPublicText("public 2001:db8:fd00::1 remains")).toContain("2001:db8:fd00::1")
    expect(redactPublicText("public ::ffff:8.8.8.8 remains")).toContain("::ffff:8.8.8.8")
    expect(redactPublicText("invalid 1:2:3:4:5:6:7:8::9 remains")).toContain("1:2:3:4:5:6:7:8::9")
  })

  it("redacts canonical IPv4-mapped private IPv6 URLs while preserving public mapped URLs", () => {
    const redacted = redactPublicText(
      "Private http://[::ffff:10.0.0.1]/, http://[::ffff:127.0.0.1]/, " +
        "http://[::ffff:169.254.10.20]/, http://[::ffff:172.16.1.2]/, and " +
        "http://[::ffff:192.168.1.68]/, http://localhost./private, and " +
        "http://nas.local./private, http://localhost\u3002/private, and " +
        "http://nas\uff0elocal\uff61/private; public http://[::ffff:8.8.8.8]/ remains.",
    )

    expect(redacted.match(/\[redacted-private-url\]/g)).toHaveLength(9)
    expect(redacted).toContain("http://[::ffff:808:808]/")
    expect(redacted).not.toContain("::ffff:a00:1")
    expect(redacted).not.toContain("::ffff:7f00:1")
    expect(redacted).not.toContain("::ffff:a9fe:a14")
    expect(redacted).not.toContain("::ffff:ac10:102")
    expect(redacted).not.toContain("::ffff:c0a8:144")
    expect(redacted).not.toContain("localhost.")
    expect(redacted).not.toContain("nas.local.")
    expect(redacted).not.toContain("localhost\u3002")
    expect(redacted).not.toContain("nas\uff0elocal\uff61")
  })

  it("neutralizes formatted mentions and numeric issue references in every Markdown context", () => {
    const redacted = redactPublicText(
      "**@everyone** _@here_ [@admins] **#123** /#456; " +
        "person@example.com and safe @ text with #topic remain readable.",
    )

    for (const activeReference of ["@everyone", "@here", "@admins", "#123", "#456"]) {
      expect(redacted).not.toContain(activeReference)
    }
    expect(redacted).toContain("[redacted-email]")
    expect(redacted).toContain("safe @ text")
    expect(redacted).toContain("#topic")
  })

  it("redacts prose credentials, authorization schemes, and underscore credential names", () => {
    const redacted = redactPublicText(
      "password is hunter2; token is token-value; api key is api-value; " +
        "Authorization: Bearer bearer-value; Authorization: Basic basic-value; " +
        "Authorization: Token auth-token-value; Basic dXNlcjpwYXNz;\n" +
        "access_token=access-value; refresh_token: refresh-value; " +
        "client_secret is client-value; private_key=private-value;\n" +
        "password reset page and token handling remain readable; " +
        "Basic setup fails and The basic configuration is broken.",
    )

    for (const value of [
      "hunter2", "token-value", "api-value", "bearer-value", "basic-value",
      "access-value", "refresh-value", "client-value", "private-value", "auth-token-value",
      "dXNlcjpwYXNz", "Bearer",
    ]) {
      expect(redacted).not.toContain(value)
    }
    expect(redacted).toContain("password reset page and token handling remain readable")
    expect(redacted).toContain("Basic setup fails and The basic configuration is broken")
    expect(redactPublicText("Basic test fails; Basic mode fails; Basic auth failed")).toBe(
      "Basic test fails; Basic mode fails; Basic auth failed",
    )
  })

  it("redacts embedded sensitive headers and structured credentials", () => {
    const redacted = redactPublicText(
      'Authorization: AWS4-HMAC-SHA256 Credential=access, SignedHeaders=host, Signature=signature-value\n' +
        'Proxy-Authorization: Custom nonce=nonce-value, response=response-value\nSafe detail remains.',
    )

    expect(redacted).toContain("Authorization=[redacted]")
    expect(redacted).toContain("Proxy-Authorization=[redacted]")
    expect(redacted).toContain("Safe detail remains.")
    for (const secret of ["access", "signature-value", "nonce-value", "response-value", "AWS4-HMAC", "Custom"]) {
      expect(redacted).not.toContain(secret)
    }
    expect(redactPublicText("Basic dXNlcjo= and Basic OnBhc3M=")).toBe(
      "[redacted-credential] and [redacted-credential]",
    )
    const embedded = redactPublicText(
      "curl -H 'Authorization: AWS4-HMAC-SHA256 Credential=access, Signature=sig' https://example.com\n" +
        "request headers: Proxy-Authorization: Custom nonce=nonce, response=response",
    )
    expect(embedded).toContain("curl -H 'Authorization=[redacted]' https://example.com/")
    expect(embedded).toContain("request headers: Proxy-Authorization=[redacted]")
    for (const secret of ["access", "Signature=sig", "nonce=nonce", "response=response"]) expect(embedded).not.toContain(secret)
    const structured = redactPublicText("payload {\"password\":\"json-password-value\",'token': 'python-token-value',\"api_key\":\"api-key-value\",\"access_token\":\"access-value\",\"refresh_token\":\"refresh-value\",\"client_secret\":\"client-value\",\"private_key\":\"private-value\",\"credential\":\"credential-value\",\"secret\":\"secret-value\",\"webhook\":\"webhook-value\",\"dsn\":\"dsn-value\"}\nlog Cookie: sid=cookie-secret\n<password>xml-secret</password><client_secret>xml-client</client_secret><dsn>xml-dsn</dsn>\ncurl -H 'Authorization: Custom trailing-secret\nprefix \"Cookie: open-cookie")
    for (const secret of ["json-password-value", "python-token-value", "api-key-value", "access-value", "refresh-value", "client-value", "private-value", "credential-value", "secret-value", "webhook-value", "dsn-value", "sid=cookie-secret", "xml-secret", "xml-client", "xml-dsn", "trailing-secret", "open-cookie"]) expect(structured).not.toContain(secret)
  })

  it("redacts complex structured values and non-ASCII email forms", () => {
    const redacted = redactPublicText('{"password":123456,"api_key":["abc123"],"client_secret":{"nested":true},"token":"abc\\\"def"} password = "abc def" authorization = Digest username=alice, response=abcdef\njosé@example.com 用户@example.com "user name"@example.com admin@localhost user@[192.168.1.5]')
    for (const value of ["123456", "abc123", "nested", "abc def", "username=alice", "abcdef", "josé", "用户", "user name", "admin@localhost", "192.168.1.5"]) expect(redacted).not.toContain(value)
  })

  it("redacts multiline structured credentials as one block", () => {
    for (const value of [
      '{\n  "secret": {\n    "value": "multiline-json-secret"\n  }\n}',
      "password: |\n  multiline-yaml-secret",
      "secret: {\n  value: multiline-unquoted-secret\n}",
      '{"secret": ["multiline-first",\n"multiline-tail"]}',
      "<password>prefix<value>\nmultiline-xml-secret\n</value></password>",
      "password:\n  indented-yaml-secret",
      "password: &vault !encrypted anchored-yaml-secret\n  anchored-yaml-tail",
      'password = """toml-first\ntoml-tail"""',
      "password = '''toml-literal-first\ntoml-literal-tail'''",
      "<password><value>nested-xml-secret</value></password>",
      "<password><value>nested-xml-first</value>\n<more>nested-xml-tail</more></password>",
    ]) expect(redactPublicText(value)).toBe("[redacted-structured-data]")
  })

  it("preserves prose around sensitive structured continuations", () => {
    const redacted = redactPublicText(
      "Before remains.\n" +
        "password = ini-first-secret\n" +
        "  ini-continuation-secret\n" +
        "After remains.\n" +
        "XML before <password><value>xml-inner-secret</value></password> XML after.",
    )
    for (const visible of ["Before remains.", "After remains.", "XML before", "XML after."]) {
      expect(redacted).toContain(visible)
    }
    for (const privateValue of ["ini-first-secret", "ini-continuation-secret", "xml-inner-secret"]) {
      expect(redacted).not.toContain(privateValue)
    }
  })

  it("redacts nested and lexically tricky sensitive XML", () => {
    for (const value of [
      '<password>outer<password>nested-secret</password>outer-tail</password>',
      '<cfg:password>outer<cfg:password>namespace-secret</cfg:password>tail</cfg:password>',
      '<password note="fake </password>"><![CDATA[cdata </password> secret]]><!-- comment </password> secret --><value>real-secret</value></password>',
    ]) {
      const redacted = redactPublicText(`Before remains. ${value} After remains.`)
      expect(redacted).toContain("Before remains.")
      expect(redacted).toContain("After remains.")
      expect(redacted.match(/\[redacted-structured-data\]/g)).toHaveLength(1)
      for (const privateValue of ["nested-secret", "namespace-secret", "cdata", "comment", "real-secret", "outer-tail"]) {
        expect(redacted).not.toContain(privateValue)
      }
    }
    expect(
      redactPublicText("Before remains. <password>case-secret</PASSWORD>case-tail</password> After remains."),
    ).toBe("Before remains. [redacted-structured-data] After remains.")
    expect(redactPublicText("Before remains.\n<password>case-secret</PASSWORD>\nunsafe-tail")).toBe(
      "Before remains.\n[redacted-structured-data]",
    )
  })

  it("redacts HCL heredocs and fails closed when unterminated", () => {
    for (const marker of ["<<EOF", "<<-EOF"]) {
      const redacted = redactPublicText(
        `Before remains.\npassword = ${marker}\nheredoc-secret\nEOF\nsafe_field = visible`,
      )
      expect(redacted).toContain("Before remains.")
      expect(redacted).toContain("safe_field = visible")
      expect(redacted).not.toContain("heredoc-secret")
      expect(redacted).not.toContain("EOF")
    }
    expect(redactPublicText("Before remains.\npassword = <<EOF\nheredoc-secret\nunsafe-tail")).toBe(
      "Before remains.\n[redacted-structured-data]",
    )
  })

  it("redacts YAML indentationless sensitive sequences to their boundary", () => {
    for (const value of [
      "password:\n- sequence-secret-one\n- sequence-secret-two",
      "password:\n- name: first\n  value: nested-secret-one\n- name: second\n  config:\n    value: nested-secret-two",
      "password:\n# private sequence follows\n- |\n  block-secret\n- key:\n    nested: mapping-secret",
    ]) expect(redactPublicText(value)).toBe("[redacted-structured-data]")

    for (const marker of ["---", "..."]) {
      const redacted = redactPublicText(
        `Before remains.\npassword:\n- sequence-secret\n- nested:\n    value: nested-secret\n${marker}\nsafe_field: visible`,
      )
      expect(redacted).toContain("Before remains.")
      expect(redacted).toContain(marker)
      expect(redacted).toContain("safe_field: visible")
      expect(redacted).not.toContain("sequence-secret")
      expect(redacted).not.toContain("nested-secret")
    }

    expect(redactPublicText("password:\n- sequence-secret\n  continuation-secret\nsafe_field: visible")).toBe(
      "[redacted-structured-data]\nsafe_field: visible",
    )
  })

  it("redacts YAML node properties before sensitive values", () => {
    for (const value of [
      "password: &anchor\n- property-secret-one\n- property-secret-two",
      "password: !vault\n- tagged-secret-one\n- tagged-secret-two",
      "password: &anchor !!seq [flow-secret-one,\nflow-secret-two]",
      "password: !<tag:example.com,2026:secret> {nested:\n  value: tagged-flow-secret}",
      "password: !!str &anchor |-\n  property-block-secret",
      "password: &anchor # sequence follows\n- commented-property-secret",
    ]) expect(redactPublicText(value)).toBe("[redacted-structured-data]")
    expect(redactPublicText("password: &anchor\n- property-secret\nsafe_field: visible")).toBe(
      "[redacted-structured-data]\nsafe_field: visible",
    )
  })

  it("redacts YAML explicit sensitive mapping keys", () => {
    for (const value of [
      "? password\n:\n  folded plain secret\n  continuation secret",
      "? 'password'\n: |\n  block scalar secret",
      '? "password"\n:\n- sequence secret one\n- nested:\n    value: sequence secret two',
      '? !!str &key-anchor "password"\n:\n  property key secret',
      "? password\n: &value-anchor # sequence follows\n- explicit property sequence secret",
      "? password\n: [flow secret one,\n  {nested: flow secret two}]",
      "? password\n:\n  nested:\n    child: nested structure secret",
    ]) expect(redactPublicText(value)).toBe("[redacted-structured-data]")
    for (const marker of ["---", "..."]) {
      const redacted = redactPublicText(
        `Before remains.\n? password\n:\n- explicit secret\n${marker}\nsafe_field: visible`,
      )
      expect(redacted).toContain("Before remains.")
      expect(redacted).toContain(marker)
      expect(redacted).toContain("safe_field: visible")
      expect(redacted).not.toContain("explicit secret")
    }
    expect(redactPublicText("? password\n:\n  explicit secret\nsafe_field: visible")).toBe(
      "[redacted-structured-data]\nsafe_field: visible",
    )
  })

  it("recursively redacts complete JSON with decoded sensitive keys", () => {
    for (const [value, privateValues, publicValues] of [
      ['{"pass\\u0077ord":"scalar-secret","safe":"visible"}', ["scalar-secret"], ["safe", "visible"]],
      ['{"safe":{"name":"visible","secr\\u0065t":{"deep":"object-secret"}},"items":[{"tok\\u0065n":["array-secret-one","array-secret-two"]}],"keep":[1,2]}', ["object-secret", "array-secret-one", "array-secret-two"], ["visible", '"keep":[1,2]']],
      ['[{"client\\u005fsecret":"nested-secret"},{"safe":"array-visible"}]', ["nested-secret"], ["array-visible"]],
      ['{\n  "safe": "pretty-visible",\n  "pass\\u0077ord": [\n    "pretty-secret"\n  ]\n}', ["pretty-secret"], ["pretty-visible"]],
    ] as Array<[string, string[], string[]]>) {
      const redacted = redactPublicText(value)
      for (const privateValue of privateValues) expect(redacted).not.toContain(privateValue)
      for (const publicValue of publicValues) expect(redacted).toContain(publicValue)
      expect(redacted).toContain("[redacted]")
    }
    const nonSensitive = '{"safe":"visible","count":2}'
    expect(redactPublicText(nonSensitive)).toBe(nonSensitive)
    const mixed = redactPublicText('Before {"password":"mixed-secret"} After')
    expect(mixed).toContain("Before")
    expect(mixed).toContain("After")
    expect(mixed).not.toContain("mixed-secret")
  })

  it("redacts YAML multiline explicit sensitive scalar keys", () => {
    for (const value of [
      "?\n  password\n:\n  - multiline-key-secret-one\n  - multiline-key-secret-two",
      '?\n  !!str &key-anchor "password"\n:\n  nested:\n    value: property-key-secret',
      "?\n  'password'\n: [flow-key-secret-one,\n  {nested: flow-key-secret-two}]",
      "?\n  !<tag:yaml.org,2002:str> password\n: |\n  block-key-secret",
    ]) expect(redactPublicText(value)).toBe("[redacted-structured-data]")
    expect(redactPublicText("?\n  password\n:\n  - multiline-key-secret\nsafe_field: visible")).toBe(
      "[redacted-structured-data]\nsafe_field: visible",
    )
    for (const marker of ["---", "..."]) {
      const redacted = redactPublicText(
        `Before remains.\n?\n  password\n:\n  - multiline-key-secret\n${marker}\nsafe_field: visible`,
      )
      expect(redacted).toContain(marker)
      expect(redacted).toContain("safe_field: visible")
      expect(redacted).not.toContain("multiline-key-secret")
    }
  })

  it("decodes YAML quoted sensitive keys before redaction", () => {
    for (const value of [
      '"pass\\u0077ord": [unicode-secret-one, unicode-secret-two]',
      '"\\x70assword": {nested: hex-secret}',
      '"\\U00000070assword": |\n  long-unicode-secret',
      '? "passw\\x6frd"\n:\n  - same-line-explicit-secret',
      '?\n  !!str &key-anchor "pass\\u0077ord"\n: [multiline-explicit-secret]',
      '?\n  "pass\\qword"\n:\n  malformed-escape-secret',
    ]) {
      const redacted = redactPublicText(value)
      expect(redacted).not.toContain("secret")
      expect(redacted).toContain("[redacted-structured-data]")
    }
    expect(redactPublicText('?\n  "pass\\u0077ord"\n:\n  - escaped-key-secret\nsafe_field: visible')).toBe(
      "[redacted-structured-data]\nsafe_field: visible",
    )
    const singleQuoted = "'pass''word': visible\n? 'pass''word'\n: still-visible"
    expect(redactPublicText(singleQuoted)).toBe(singleQuoted)
    const standardEscapes = '"safe\\/key": visible\n? "safe\\tkey"\n: still-visible'
    expect(redactPublicText(standardEscapes)).toBe(standardEscapes)
  })

  it("redacts decoded sensitive keys in embedded JSON", () => {
    for (const [value, privateValue, publicValues] of [
      ['Before {"pass\\u0077ord":"embedded-secret"} After', "embedded-secret", ["Before", "After"]],
      ['Prefix [{"safe":"visible"},{"tok\\u0065n":["array-secret"]}] Suffix', "array-secret", ["Prefix", "visible", "Suffix"]],
      ['Before {\n  "safe": {"name": "nested-visible"},\n  "secr\\u0065t": {"value": "nested-secret"}\n} After', "nested-secret", ["Before", "nested-visible", "After"]],
    ] as Array<[string, string, string[]]>) {
      const redacted = redactPublicText(value)
      expect(redacted).not.toContain(privateValue)
      for (const publicValue of publicValues) expect(redacted).toContain(publicValue)
    }
  })

  it("classifies composite sensitive structured keys", () => {
    const complete = redactPublicText(
      '{"github_token":"github-secret","database_password":"database-secret","smtpPassword":"smtp-secret","webhook_url":"webhook-secret","token_count":4,"password_policy":"strict","safe":"visible"}',
    )
    for (const privateValue of ["github-secret", "database-secret", "smtp-secret", "webhook-secret"]) {
      expect(complete).not.toContain(privateValue)
    }
    for (const publicValue of ['"token_count":4', '"password_policy":"strict"', '"safe":"visible"']) {
      expect(complete).toContain(publicValue)
    }
    const embedded = redactPublicText(
      'Before {"nested":{"provider.notification_credentials":"provider-secret","clientSecret":"client-secret"},"session_count":2} After',
    )
    expect(embedded).not.toContain("provider-secret")
    expect(embedded).not.toContain("client-secret")
    for (const publicValue of ["Before", '"session_count":2', "After"]) expect(embedded).toContain(publicValue)

    const yaml = redactPublicText(
      "github_token: yaml-token-secret\n" +
      "database_password:\n  nested-password-secret\n" +
      "smtpPassword: {value: smtp-password-secret}\n" +
      "webhook_url: |\n  webhook-body-secret\n" +
      "? notificationCredential\n:\n  provider-notification-secret\n" +
      "token_count: 4\npassword_policy: strict",
    )
    for (const privateValue of ["yaml-token-secret", "nested-password-secret", "smtp-password-secret", "webhook-body-secret", "provider-notification-secret"]) {
      expect(yaml).not.toContain(privateValue)
    }
    expect(yaml).toContain("token_count: 4")
    expect(yaml).toContain("password_policy: strict")
  })

  it("redacts provider access identifier composites", () => {
    const complete = redactPublicText(
      '{"AWSAccessKeyId":"aws-camel-secret","aws_access_key_id":"aws-snake-secret","GoogleAccessId":"google-access-secret","KeyPairId":"key-pair-secret","access_key_count":4,"access_policy":"read-only","provider_id":"visible-id"}',
    )
    for (const privateValue of ["aws-camel-secret", "aws-snake-secret", "google-access-secret", "key-pair-secret"]) {
      expect(complete).not.toContain(privateValue)
    }
    for (const publicValue of ['"access_key_count":4', '"access_policy":"read-only"', '"provider_id":"visible-id"']) {
      expect(complete).toContain(publicValue)
    }
    const embedded = redactPublicText(
      'Before {"provider":{"AWSAccessKeyId":{"value":"nested-provider-secret"}},"key_pair_count":2} After',
    )
    expect(embedded).not.toContain("nested-provider-secret")
    for (const publicValue of ["Before", '"key_pair_count":2', "After"]) expect(embedded).toContain(publicValue)

    const yaml = redactPublicText(
      "AWSAccessKeyId: yaml-aws-secret\n" +
      "GoogleAccessId:\n  yaml-google-secret\n" +
      "KeyPairId: [yaml-pair-secret]\n" +
      "access_key_count: 4\nkey_pair_count: 2\nprovider_id: visible-id",
    )
    for (const privateValue of ["yaml-aws-secret", "yaml-google-secret", "yaml-pair-secret"]) {
      expect(yaml).not.toContain(privateValue)
    }
    for (const publicValue of ["access_key_count: 4", "key_pair_count: 2", "provider_id: visible-id"]) {
      expect(yaml).toContain(publicValue)
    }
    const xml = redactPublicText(
      "Before <AWSAccessKeyId><value>xml-provider-secret</value></AWSAccessKeyId> After",
    )
    expect(xml).not.toContain("xml-provider-secret")
    expect(xml).toContain("Before")
    expect(xml).toContain("After")

    const fused = redactPublicText(
      '{"githubtoken":"fused-github-secret","databasepassword":"fused-database-secret","smtppassword":"fused-smtp-secret","notificationcredentials":"fused-notification-secret","tokencount":3,"passwordpolicy":"strict"}',
    )
    for (const privateValue of ["fused-github-secret", "fused-database-secret", "fused-smtp-secret", "fused-notification-secret"]) {
      expect(fused).not.toContain(privateValue)
    }
    expect(fused).toContain('"tokencount":3')
    expect(fused).toContain('"passwordpolicy":"strict"')

    const inline = redactPublicText(
      "Before github_token: inline-token-secret After\n" +
      "log database_password=inline-password-secret other\n" +
      'trace smtpPassword="inline smtp secret" remains',
    )
    for (const privateValue of ["inline-token-secret", "inline-password-secret", "inline smtp secret"]) {
      expect(inline).not.toContain(privateValue)
    }
    expect(inline).toContain("Before github_token: [redacted] After")
    expect(inline).toContain("log database_password=[redacted] other")
    expect(inline).toContain("trace smtpPassword=[redacted] remains")

    const fusedYamlXml = redactPublicText(
      "githubtoken: fused-yaml-secret\ntokencount: 3\npasswordpolicy: strict\n" +
      "Before <notificationcredentials>fused-xml-secret</notificationcredentials> After",
    )
    expect(fusedYamlXml).not.toContain("fused-yaml-secret")
    expect(fusedYamlXml).not.toContain("fused-xml-secret")
    expect(fusedYamlXml).toContain("tokencount: 3")
    expect(fusedYamlXml).toContain("passwordpolicy: strict")
  })

  it("redacts account keys, queries, and inline credential forms", () => {
    const parsedQuery = JSON.parse(redactPublicText(
      '{"url":"/callback?token=abc","password":"other-secret","safe":"visible"}',
    ))
    expect(parsedQuery).toEqual({ url: "/callback?token=[redacted]", password: "[redacted]", safe: "visible" })
    const parsedArray = JSON.parse(redactPublicText('["/callback?token=array-secret",{"safe":"visible"}]'))
    expect(parsedArray).toEqual(["/callback?token=[redacted]", { safe: "visible" }])
    const parsedFullUrl = JSON.parse(redactPublicText(
      '{"url":"https://public.example/callback?databasepassword=url-json-secret&ok=1","safe":"visible"}',
    ))
    expect(parsedFullUrl.url).not.toContain("url-json-secret")
    expect(parsedFullUrl.url).toContain("ok=1")
    expect(parsedFullUrl.safe).toBe("visible")
    const punctuation = redactPublicText(
      'See (/callback?token=paren-secret). next; quoted "/callback?token=quote-secret", ' +
      "backticked `/callback?token=tick-secret`; [/callback?token=bracket-secret] {/callback?token=brace-secret}.",
    )
    for (const privateValue of ["paren-secret", "quote-secret", "tick-secret", "bracket-secret", "brace-secret"]) {
      expect(punctuation).not.toContain(privateValue)
    }
    for (const delimiter of ["). next", '"', "`", "]", "}."]) expect(punctuation).toContain(delimiter)

    const safeIsProse = "Auth is failing today\nSession is expired after reboot\nSignature is invalid\n" +
      '{"message":"Auth is failing today; Session is expired after reboot; Signature is invalid","safe":"visible"}'
    expect(redactPublicText(safeIsProse)).toBe(safeIsProse)

    const complete = redactPublicText(
      '{"accountKey":"account-secret","storageAccountKey":"storage-secret","accountkey":"fused-account-secret",' +
      '"connectionString":"DefaultEndpointsProtocol=https;AccountName=visible;AccountKey=connection-secret;EndpointSuffix=core.windows.net",' +
      '"accesskey":"direct-access-key","secretkey":"direct-secret-key",' +
      '"signature":"signature-secret","x-amz-signature":"amz-signature-secret","awssecretaccesskey":"aws-secret-access-key",' +
      '"azureaccesskeyid":"azure-access-id","oracleaccesskeyid":"oracle-access-id","s3accesskeyid":"s3-access-id",' +
      '"provideraccessid":"provider-access-id","sessionid":"session-secret","authkey":"auth-secret",' +
      '"hmackeypairid":"hmac-pair-secret","githubapikey":"github-api-secret","githubprivatekey":"github-private-secret",' +
      '"codesigningkey":"signing-secret","discordwebhookurl":"discord-webhook-secret","sentrydsn":"sentry-dsn-secret",' +
      '"account_key_count":2,"accesskeycount":3,"access_policy":"visible-policy","providerid":"visible-provider"}',
    )
    for (const privateValue of [
      "account-secret", "storage-secret", "fused-account-secret", "connection-secret", "direct-access-key", "direct-secret-key", "signature-secret",
      "amz-signature-secret", "aws-secret-access-key", "azure-access-id", "oracle-access-id", "s3-access-id",
      "provider-access-id", "session-secret", "auth-secret",
      "hmac-pair-secret", "github-api-secret", "github-private-secret", "signing-secret",
      "discord-webhook-secret", "sentry-dsn-secret",
    ]) expect(complete).not.toContain(privateValue)
    for (const publicValue of [
      "AccountName=visible", "EndpointSuffix=core.windows.net", '"account_key_count":2', '"accesskeycount":3',
      '"access_policy":"visible-policy"', '"providerid":"visible-provider"',
    ]) expect(complete).toContain(publicValue)

    const structured = redactPublicText(
      "accountKey: yaml-account-secret\nstorageAccountKey: yaml-storage-secret\naccount_key_count: 2\n" +
      "Before <accountKey>xml-account-key</accountKey><secretkey>xml-secret-key</secretkey> After",
    )
    for (const privateValue of ["yaml-account-secret", "yaml-storage-secret", "xml-account-key", "xml-secret-key"]) {
      expect(structured).not.toContain(privateValue)
    }
    expect(structured).toContain("account_key_count: 2")
    expect(structured).toContain("Before")
    expect(structured).toContain("After")

    const inline = redactPublicText(
      "Azure DefaultEndpointsProtocol=https;AccountName=visible;AccountKey=inline-account-secret;EndpointSuffix=core.windows.net remains\n" +
      "github_token=Bearer bearer-secret after\n" +
      "database_password:=Basic basic-secret next\n" +
      "authkey=>Token token-secret done\n" +
      "github_token is word-secret final\n" +
      'Before github_token: "unterminated-secret\n' +
      "Before database_password='unterminated-password",
    )
    for (const privateValue of [
      "inline-account-secret", "bearer-secret", "basic-secret", "token-secret", "word-secret",
      "unterminated-secret", "unterminated-password",
    ]) expect(inline).not.toContain(privateValue)
    for (const publicValue of ["AccountName=visible", "EndpointSuffix=core.windows.net", "after", "next", "done", "final"]) {
      expect(inline).toContain(publicValue)
    }

    const queries = redactPublicText(
      "/callback?github_token=query-secret&ok=1\n" +
      "https://public.example/callback?databasepassword=url-secret&ok=2\n" +
      "/callback?github%5Ftoken=encoded-secret&safe=3\n" +
      "/download?accesskey=access-key-secret&account_key_count=4&providerid=visible",
    )
    for (const privateValue of ["query-secret", "url-secret", "encoded-secret", "access-key-secret"]) {
      expect(queries).not.toContain(privateValue)
    }
    for (const publicValue of ["ok=1", "ok=2", "safe=3", "account_key_count=4", "providerid=visible"]) {
      expect(queries).toContain(publicValue)
    }

    const boundarySafeKey = "a".repeat(256)
    const boundarySensitiveKey = "a".repeat(251) + "token"
    const overlongKey = "a".repeat(252) + "token"
    const veryLongKey = "z".repeat(10_000)
    const encodedOverlongKey = "a".repeat(252) + "%74oken"
    const queryLengths = redactPublicText(
      `/cb?${boundarySafeKey}=BOUNDARY_VISIBLE&ok=1\n` +
      `/cb?${boundarySensitiveKey}=BOUNDARY_SECRET&ok=2\n` +
      `/cb?${overlongKey}=OVERLONG_VALUE&ok=3\n` +
      `/cb?${veryLongKey}=VERY_LONG_VALUE&ok=4\n` +
      `/cb?${encodedOverlongKey}=ENCODED_OVERLONG_VALUE&ok=5`,
    )
    expect(queryLengths).toContain("BOUNDARY_VISIBLE")
    for (const privateValue of ["BOUNDARY_SECRET", "OVERLONG_VALUE", "VERY_LONG_VALUE", "ENCODED_OVERLONG_VALUE"]) {
      expect(queryLengths).not.toContain(privateValue)
    }
    for (const safeParameter of ["ok=1", "ok=2", "ok=3", "ok=4", "ok=5"]) {
      expect(queryLengths).toContain(safeParameter)
    }
  })

  it("sanitizes diagnostics shown in the browser-visible public preview", () => {
    const rows = publicDiagnosticsRows({
      channelwatch_version: "0.9.16 ![v][pixel]",
      dvr_count: 1,
      connected_dvr_count: 1,
      core_status: "fatal admin@example.com fd00::1234",
      monitoring_statuses: ["live token=hunter2", "![m](https://evil.example/m)"],
      notification_providers: ["@everyone #123"],
      feature_toggles: {
        channel_watching: true,
        vod_watching: false,
        disk_space: false,
        recording_events: false,
        stream_counter: false,
      },
    })
    const visiblePreview = rows.flat().join(" ")

    expect(visiblePreview).toContain("fatal")
    for (const privateValue of [
      "admin@example.com",
      "fd00::1234",
      "hunter2",
      "evil.example",
      "@everyone",
      "#123",
      "![",
    ]) {
      expect(visiblePreview).not.toContain(privateValue)
    }
  })
})
