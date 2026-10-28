> 1.2.0 candidate: embeds Controller 1.2.0 and explicitly accepts updates from 1.1.1, the currently observed LIME version. Candidate CI now installs the published 1.1.1 predecessor and verifies settings/data preservation on amd64 and arm64. Real-machine updates, AnimeGen graph registration and GPU acceptance remain separate. Controller is embedded; install/update only Generation MCP on LIME.

> 1.0.10 matches Studio 1.0.10 and Controller 1.0.7 for automatic speech duration. Publication/live acceptance are separate.

> Release target 1.0.9 embeds Controller 1.0.6. Reconcile settled unknown speech before updating; installation preserves pending reservations and explicit timeout settings. Source validation is separate from publication and actual-host acceptance.

# Repository-owned Updater release 1.0.9

インストールを始める前に、[入力値の用途・取得元・自動生成と導入後の設定](FIRST_SETUP.md)を確認してください。Updaterの項目名だけで値を推測せず、このアプリの手順に沿って進めます。

このリポジトリがソース・ビルド・Release・配布物・インストールカタログを管理します。Updaterは配布物を集め直さず、カタログに記載したこのリポジトリのReleaseから、選択したアプリとCPU向けのファイルだけを取得します。

公開成功後のカタログURL：
`https://github.com/flamoris-jp/flamoris-generation-mcp/releases/download/v1.0.9/catalog.json`

`.github/workflows/publish-updater-release.yml` はこのアプリだけをamd64/arm64でビルドし、固定image IDまたは完全なoffline wheelhouse、ファイルのSHA-256、設定と起動方法をカタログへ記録します。`release/install-recipe.json` がアプリ所有のインストール定義です。公開前にUpdater自身の型と実際のprofile展開処理で検証します。検証用のUpdater依存はCIツール環境だけに入れ、アプリのランタイムへは追加しません。

Release/tagが存在する場合は上書きしません。以前の集中配布と別のパッチ版で、URL・バイト列・カタログ内容の衝突を避けます。`compatible_from` は1.0.2から1.0.8までを宣言します。設定ファイル、既存mount、Job・Workflowの保存形式を維持して実行imageを更新します。1.0.8からの更新は、公開済み旧imageを使ったCIで設定・保存ファイルの維持と新imageの起動を確認します。その他の宣言済み互換版について、同じ更新検証を実施済みとは主張しません。

Controller 1.0.6はこのイメージ内の固定依存ライブラリです。カタログの`components`とイメージの版情報を一致させるため、Updater 1.0.10以降が必要です。別のControllerサービスを起動しません。ComfyUI・モデル・Irodori・Music等の既存ランタイムとそのプロファイルは別途設定します。

Updater 1.0.10以降へ更新後、このURLをカタログ画面へ登録して個別に導入します。アプリ自身の接続トークン・DBパスワード等は必要な設定であり、Updaterのログイン認証とは別です。初回導入後の設定、実際のプロキシ/トンネル、GPU・モデル動作は運用時に確認します。CI・公開成功を実機受入として報告しません。設定・DB・データは実行ファイルの更新対象から分離します。

従来の高度なOwner/移行用SDKは内部統合用としてソースに残りますが、通常のこの新規インストールにOwnerプロファイル・mTLS・DBバックアップ・旧配置の取り込みは要求しません。

## Generation 1.0.3 prerequisites

Generation 1.0.3 catalogs declare embedded Controller 1.0.1. Upgrade Updater to 1.0.9 or newer before installing/updating this catalog; older Updater versions reject the component declaration. Configure ComfyUI nodes/models and shared input storage as described in COMFY_REGISTRATION.md and MANAGED_INPUTS.md. No model downloads or real-host configuration are performed by the source change.
