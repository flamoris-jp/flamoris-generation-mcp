# Contributing to FLAMORIS Generation MCP

Thank you for your interest in FLAMORIS.

FLAMORIS Generation MCP is a small, MCP-native media-generation server. Please keep changes focused, provider-neutral where practical, and small enough to understand.

## Before contributing

For small fixes, feel free to open a pull request directly.

For larger changes, new providers, new public MCP tools, transport changes, or architecture changes, please open an issue first so the intended boundary can be discussed before implementation.

Keep provider-specific behavior behind adapters and avoid speculative abstractions for media types or providers that are not yet implemented.

## Pull requests

Please:

- keep changes focused;
- include or update tests where practical;
- explain architectural or compatibility impact;
- preserve existing public behavior unless the change intentionally modifies it;
- avoid introducing unnecessary dependencies;
- document any new externally visible behavior.

AI-assisted contributions are welcome. The contributor remains responsible for reviewing, testing, and understanding the submitted change.

## Licensing

Unless explicitly stated otherwise, code contributions are submitted under the Apache License 2.0.

Do not add third-party code, models, datasets, media, or other assets unless their licenses are compatible and clearly documented.

## Support

FLAMORIS does not provide guaranteed individual support.

If you are working through a problem, please use the repository documentation, issues, tests, and source code as primary references. AI-assisted self-support is encouraged.

---

# FLAMORIS Generation MCP へのコントリビューション

FLAMORISに興味を持っていただきありがとうございます。

FLAMORIS Generation MCPは、MCPネイティブなメディア生成サーバーです。変更は、役割が明確で、可能な範囲でprovider-neutralで、理解しやすい大きさを保ってください。

## 変更を始める前に

小さな修正は、そのままPull Requestを送っていただいて構いません。

大きな変更、新しいprovider、新しい公開MCP tool、transport変更、アーキテクチャ変更は、実装前にIssueで意図や境界を相談してください。

provider固有の処理はadapterの後ろに置き、まだ実装していないmedia typeやproviderのための先回りした抽象化は避けます。

## Pull Request

以下を意識してください。

- 変更範囲を絞る
- 可能な範囲でテストを追加・更新する
- アーキテクチャや互換性への影響を書く
- 意図的な変更でない限り、既存の公開動作を壊さない
- 不要な依存関係を増やさない
- 外部から見える新しい挙動は文書化する

AIを使ったコントリビューションも歓迎します。提出する変更の確認、テスト、内容の理解については、コントリビュータ自身が責任を持ってください。

## ライセンス

明記がない限り、コードへのコントリビューションはApache License 2.0の条件で提供されます。

第三者のコード、AIモデル、データセット、画像・音声などの素材を追加する場合は、互換性のあるライセンスであることを確認し、そのライセンスを明示してください。

## サポート

FLAMORISは個別サポートを保証しません。

困ったときは、README、ドキュメント、Issue、テスト、ソースコードを主な参照先として使ってください。AIによる自己サポートも歓迎します。
