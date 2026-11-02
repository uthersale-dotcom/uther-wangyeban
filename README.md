Uther Web Order

手机自适应网页下单，部署在 Railway。

环境变量





BOT_TOKEN — Telegram 机器人 token（用于发订单提醒）



ADMIN_CHAT_ID — 管理员 Telegram ID（默认 5922115358）



PORT — Railway 自动设置



DATA_DIR — 数据目录，默认 /data

部署





把这个文件夹推到 GitHub，或直接在 Railway 新建服务选这个目录。



设置环境变量 BOT_TOKEN 和 ADMIN_CHAT_ID。



如需共享原机器人的商品数据，挂载同一个 Volume 到 /data。



部署后访问首页下单，/admin 查看待发货并回传跟踪号。

功能





选仓库、选商品、填地址、看付款地址



下单后自动发 Telegram 给管理员



/admin 页面输入跟踪号，标记已发货并通知管理员

