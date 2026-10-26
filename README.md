# 国民の目 公式公開データ

既存のSites/D1を維持したまま追加する、公式APIメタデータのSQLite保存・JSON配信基盤です。秘密情報・個人設定・既存サイトのソースは含みません。

## 取得範囲

- e-Gov法令API v2：日本国憲法、公職選挙法、地方自治法、教育基本法、財政法の現行改訂メタデータ
- 統計ダッシュボードAPI：総人口の年次値（2015年以降）、全国・東京・大阪・神奈川・兵庫・千葉・埼玉。e-Stat標準APIとは別の登録不要API
- 国会会議録検索API：直近60日から最大20会議のメタデータ。全件網羅ではない。発言本文を再配布しない

Python 3標準ライブラリのみ。`python collect.py`、`python -m unittest discover -s tests -v`。3秒間隔・逐次取得・ページ上限・指数バックオフ・30秒タイムアウト・3MB上限。SQLiteの複合キーで重複を防止し、値の変更時だけ履歴を追加。取得失敗時は直前のデータを保持して失敗を記録します。データ不在は取り消しとは断定せず過去の確認日時を維持します。

## 定期実行

公開リポジトリのGitHub Actionsを使用。公開JSONをGitHubのraw配信から既存Sitesが読み取ります。Pagesは不要です。初回はコード登録で自動実行し、以後は手動再実行もできます。予定は日本時間6:17・18:17。無料の標準runnerのみ、有料サービス契約やAI APIは不要です。予定時刻は実行保証ではなく、GitHubの遅延・停止・利用制限に従います。失敗と更新履歴はActionsおよび公開JSONで確認します。外部通知サービスは未接続です。

公開件数3000件、レスポンス3MBを超える場合は処理を止めます。全国全指標の無制限収集や大容量ファイルは対象外です。SQLiteをGitで蓄積するため、長期運用では容量を確認してください。

## 出典・利用条件

- https://kokkai.ndl.go.jp/api.html
- https://laws.e-gov.go.jp/api/2/swagger-ui/
- https://dashboard.e-stat.go.jp/static/api
- https://dashboard.e-stat.go.jp/static/sitePolicy

このサービスは、統計ダッシュボードのAPI機能を使用していますが、サービスの内容は国によって保証されたものではありません。

JSONの各レコードに出典・確認日時、統計には年・地域・単位・速報/確報・注記を保持します。標準e-StatのappIdは未発行・未設定です。


## 国民負担率

`python burden.py`は財務省の最新公表ページを検出し、公式PDFの年度別系列を構造化します。`pip install -r requirements.txt`でpypdfを準備してください。公表日・対象年度・実績/実績見込み/見通し、国民所得を分母にする割合、原表URL・PDFハッシュを保存します。`public/burden.sqlite`に改訂履歴、`public/burden.json`に表示用データ、`public/burden-status.json`に取得成否を保存します。既存の毎日6:17/18:17 JSTの更新で併せて確認します。失敗時は前回の有効データを維持してエラーを記録します。

出典: https://www.mof.go.jp/policy/budget/topics/futanritsu/index.htm 。利用条件: https://www.mof.go.jp/about_mof/notice/index.html 。公式表を加工した表示であり、財務省提供のサイトではありません。個人向け試算の税制ルールは、この国全体の系列とは分離してSites側で版管理・レビューします。
