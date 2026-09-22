# Security Policy

## Reporting a vulnerability

Please do not post suspected security vulnerabilities, credentials, tokens, personal data, or other sensitive information in a public issue.

If GitHub private vulnerability reporting is available for this repository, please use it.

If private reporting is not available, avoid publishing exploit details or secrets publicly. Contact the FLAMORIS maintainers through an appropriate private channel before disclosing sensitive details.

For non-sensitive security hardening, dependency updates, or general security discussions, a normal GitHub issue is welcome.

## Generation-specific security scope

Generation MCP crosses trust boundaries between MCP clients, providers, model libraries, workflows, local files, generated assets, and optional HTTP/tunnel deployment.

Treat the following as security-sensitive:

- provider credentials and API keys;
- prompts and generation parameters that may contain private information;
- MCP HTTP exposure, reverse proxies, and tunnel configuration;
- provider responses, filenames, paths, and metadata;
- workflow/template injection;
- arbitrary filesystem access or path traversal;
- generated or uploaded media that may be private;
- model and dataset provenance;
- resource exhaustion from generation, analysis, large assets, or concurrency;
- retry and cancellation behavior for non-idempotent provider operations.

Provider responses and model-generated metadata are untrusted input. Validate paths, identifiers, MIME types, sizes, and control data before using them.

Do not commit live credentials, private deployment details, model weights, private datasets, or private generated media.

## Supported versions

FLAMORIS is developed as an open-source project without a guaranteed support window or security-response SLA.

Security fixes are generally applied to the current maintained codebase rather than to every historical version.

## Scope

This policy applies to code maintained by FLAMORIS.

Third-party dependencies, AI models, model weights, datasets, services, and media assets may have their own security and support policies.

---

# セキュリティポリシー

## 脆弱性の報告

脆弱性の可能性がある情報、認証情報、トークン、個人情報、その他の機密情報を公開Issueへ投稿しないでください。

このリポジトリでGitHubのPrivate vulnerability reportingが利用できる場合は、そちらを使用してください。

Private reportingが利用できない場合も、攻撃手順や秘密情報を公開せず、機密情報を共有する前にFLAMORISのメンテナへ適切な非公開手段で連絡してください。

機密性のないセキュリティ改善、依存関係の更新、一般的なセキュリティ議論については、通常のGitHub Issueを利用して構いません。

## Generation MCP固有の注意点

Generation MCPはMCP client、provider、model library、workflow、local file、generated asset、HTTP/tunnel deploymentの間をまたぎます。

特にprovider credential、private prompt、MCP HTTP公開範囲、tunnel設定、provider response、filename/path、workflow injection、path traversal、private media、model/datasetの出所、resource exhaustion、non-idempotent operationのretry/cancellationをsecurity-sensitiveとして扱ってください。

Provider responseやmodel由来metadataは信頼済み入力として扱わず、path、identifier、MIME type、size、control dataを検証してください。

live credential、個人環境のdeployment情報、model weights、private dataset、非公開生成物はcommitしないでください。

## サポート対象

FLAMORISはオープンソースプロジェクトとして開発されており、サポート期間やセキュリティ対応時間を保証していません。

セキュリティ修正は、原則として現在保守しているコードベースへ適用します。

## 対象範囲

このポリシーはFLAMORISが管理するコードに適用されます。

第三者の依存ライブラリ、AIモデル、weights、データセット、外部サービス、メディア素材などには、それぞれ別のセキュリティ方針やサポート条件が適用される場合があります。
