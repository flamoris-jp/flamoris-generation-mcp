# Generation MCP／Controller：Updaterの入力値と導入後の設定

対象は1.0.3のインストール定義です。別の版では選定Releaseのカタログと[インストール定義](../release/install-recipe.json)を照合します。
Updaterの画面には短い項目名が表示されます。本書で用途・取得元・空欄時の動作を確認してください。
既定値は入力欄の初期値で、generate=true以外の欄を空にしても自動生成されません。
共通のDB・秘密値の扱い、配置の調べ方、作業の順番は[共通設定ガイド](https://github.com/flamoris-jp/flamoris-updater/blob/main/docs/APPLICATION_SETUP.md)にあります。

## 導入前に用意するもの

動作するComfyUIと既存のモデルフォルダを用意します。Controllerはこのimage内の固定libraryで、別daemonとして導入しません。

## Updaterに入力する値

| 項目（キー） | 用途・値の用意 | 初期表示／空欄の動作 |
| --- | --- | --- |
| 待受ポート（PORT） | Generationの外部MCPとController内部HTTPをhostingするサービスの入口。未使用のポートを選ぶ | 8765 |
| ComfyUIのURL（COMFYUI_URL） | 既存ComfyUIのservice設定から取得。Generation実行環境から到達できるbase URL | http://127.0.0.1:8188 |
| モデルフォルダ（MODEL_ROOT） | 導入ホスト上の既存モデルdirectoryを確認して指定。image内の/data/modelsへ読み取り専用mountする | 必須。モデルのdownload／配置は別作業 |
| Controller接続トークン（CONTROLLER_TOKEN） | Studio backendがController内部HTTPへ接続する共有秘密値。Generation側で作り、StudioのGENERATION_TOKENへ同じ値を入れる | 空欄でUpdaterが生成。外部サイトから取得しない |

CONTROLLER_TOKENはruntime.envのFLAMORIS_CONTROLLER_TOKENに保存されます。
これはHubの入口tokenやOpenAI API keyとは別です。Studio側で新しく生成してはいけません。
生成値の所在は[共通設定ガイド](https://github.com/flamoris-jp/flamoris-updater/blob/main/docs/APPLICATION_SETUP.md#自動生成された値はどこにあるか)を参照してください。

## 導入後にユーザー／AIが行うこと

1. Updaterで起動する。ComfyUIの到達性、モデル名とfolderの対応、共有input領域・必要な保存領域と権限を実際の配置で確認する。
2. [Controller API](https://github.com/flamoris-jp/flamoris-generation-controller/blob/main/docs/API.md)と設定を確認する。StudioのGENERATION_ENDPOINTには正確な/api/v1/generationのbaseを指定し、tokenを一致させる。外部MCPの/mcpとは別の入口。
3. 外部ChatGPTから使う場合だけ、Hubにこのサービスのmatched tool catalog・外部endpointを設定する。外部provenanceは[専用契約](EXTERNAL_PROVENANCE.md)へ進む。
4. 基本Imageのモデル発見、選定した生成、結果取得を順に確認する。[登録ComfyWorkFlow](COMFY_REGISTRATION.md)や参照画像の実行は対応profileの別確認。
5. Speech／Musicを使う場合だけ、[Irodori](IRODORI_PROVIDER.md)、[Music](MUSIC_PROVIDERS.md)、[SheetSage2](SHEETSAGE2.md)の既存runtime・provider設定を行う。最初の4入力だけで全providerが利用可能になるわけではない。

## 完了の見分け方

プロセス起動、ComfyUI／model接続、Studio接続、対象生成の成功を分けて記録します。
ComfyUI・GPU runtimeの起動停止は正規Managerが担当し、接続URLやtokenの確認のために全runtimeを切り替えません。
