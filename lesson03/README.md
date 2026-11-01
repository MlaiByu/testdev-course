# 玄武商城 · 接口自动化测试套件

针对 **玄武商城**（FastAPI + PostgreSQL，开发环境实测 PG 18.6）的接口自动化测试，覆盖认证、商品、购物车、地址、订单、协议规范六个维度。

> **30 条用例（29 passed + 1 xfailed），全量 12 秒跑完，Allure 报告一键生成。**  
> 测试全程走 HTTP 黑盒，**不直连数据库**——所以后端换连接池、加索引、改表结构，用例完全不用动。

---

## 一、快速开始

### 1. 前置：启动被测服务

```powershell
cd C:\Users\12607\Desktop\xuanwu\backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
```

开一个独立窗口让它一直跑着，然后验证：

```powershell
curl.exe http://127.0.0.1:8000/healthz
# {"code":0,"message":"ok","data":{"status":"up","env":"dev"}}
```

> 为什么用 `curl.exe` 而不是 `curl`：PowerShell 里 `curl` 是 `Invoke-WebRequest` 的别名，输出格式完全不同。

### 2. 跑测试

```powershell
cd C:\Users\12607\Desktop\测试开发学习\lesson03

# 全量
..\.venv\Scripts\python.exe -m pytest -q

# 只跑某一组
..\.venv\Scripts\python.exe -m pytest -k order -v

# 生成 Allure 报告
..\.venv\Scripts\python.exe -m pytest --alluredir=allure-results
allure serve allure-results                    # 起服务看（自动开浏览器）
allure generate allure-results -o allure-single --clean --single-file   # 单文件，可发人
```

---

## 二、被测系统速览（结论都来自 `docs/API-CONTRACT.md` 与后端源码）

| 项    | 事实                                                                                                 |
| ---- | -------------------------------------------------------------------------------------------------- |
| 后端   | FastAPI 0.115 + SQLAlchemy 2.0 + Alembic，Python 3.12                                               |
| 数据库  | **仅支持 PostgreSQL**（开发环境实测 18.6），`DATABASE_URL` 缺失则启动失败                                             |
| 鉴权   | 登录下发 httpOnly cookie `access_token`（`HttpOnly; SameSite=Lax; Path=/`），同时兼容 `Authorization: Bearer` |
| 角色   | `customer` / `merchant` / `admin` / `super_admin`（四层鉴权在 `deps.py`）                                 |
| 接口前缀 | `/api/v1`，健康检查 `/healthz`，文档 `/docs`（非生产）                                                          |

### 铁律：`HTTP 2xx ⟺ code == 0`

项目明确禁止「HTTP 200 + 业务错误码」。**这条铁律决定了断言的第 ①② 层必须成对出现**：

```python
assert r.status_code == 200     # ① 链路层
assert body["code"] == 0        # ② 业务层 —— 少这一条，网关返回的 HTML 200 页面也能过
```

常用错误码（完整表见 `docs/API-CONTRACT.md`）：

| code    | HTTP | 含义                     | 对应我们的用例           |
| ------- | ---- | ---------------------- | ----------------- |
| `40001` | 400  | 参数校验失败                 | 非法密码 / 非法手机号      |
| `40100` | 401  | 未登录                    | 未带凭证访问 `/auth/me` |
| `40101` | 401  | 凭证无效或过期                | 伪造/篡改 JWT         |
| `40400` | 404  | 资源不存在（**归属失败也用它，防枚举**） | 越权访问他人订单          |
| `40900` | 409  | 资源已存在                  | 重复注册              |
| `40901` | 409  | 库存不足                   | 下单库存不足            |
| `40902` | 409  | 当前状态不支持该操作             | 重复支付 / 取消已支付      |
| `40903` | 409  | 重复提交（幂等键冲突）            | 同 key 不同 body     |

---

## 三、目录结构

```
lesson03/
├── conftest.py           # 测试基座：常量 + 工具函数 + 9 个 fixture
├── test_register.py      # 注册组  13 条
├── test_login.py         # 登录组   6 条
├── test_order.py         # 订单组   8 条
├── test_protocol.py      # 协议层   3 条
├── 接口自动化入门.md      # 第 3 课讲义
└── README.md
```

---

## 四、用例清单

| 文件                 | 数量 | 覆盖内容                                                                             |
| ------------------ | -- | -------------------------------------------------------------------------------- |
| `test_register.py` | 13 | 正常注册 / 4 组非法密码 / **5 组非法手机号** / 空串手机号归一 / 重复邮箱 / XSS 昵称                          |
| `test_login.py`    | 6  | 正常登录（cookie + JWT 内容）/ 密码错误 / 账号不存在（反枚举）/ Bearer 头兼容 / 登出清 cookie / **改密后旧凭证失效**                |
| `test_order.py`    | 8  | 下单成功 / 支付 / 重复支付 40902 / 取消已支付 40902 / **库存不足 40901（自愈）** / 越权 40400 / **幂等 ×2** |
| `test_protocol.py` | 3  | **401 缺 WWW-Authenticate（xfail）** / Content-Type / x-trace-id                    |

合计 **30 条**：29 passed + 1 xfailed。

---

## 五、六个设计要点

### 1. 断言分层（失败时一眼定位层级）

```
status_code → body.code → 业务字段 → 契约(不该出现的字段)
            → 凭证内容(payload) → 活性(拿凭证真调一次接口)
```

- ① 挂 = 链路/路由问题
- ② 挂 = 业务码与契约不符
- ③ 挂 = 数据处理逻辑错
- ④ 挂 = 安全契约被破坏
- ⑥ 挂 = 凭证签发出来但不可用（**只有这一层能证明「登录真的成功」**）

### 2. fixture 作用域

| fixture                         | 作用域          | 理由                                  |
| ------------------------------- | ------------ | ----------------------------------- |
| `base_url`                      | session      | 常量，全场不变                             |
| `api`                           | **function** | 会存 cookie，共享会污染（实测共享只省 0.75ms/次，不值） |
| `unique_email` / `unique_phone` | function     | 有唯一约束，共享 = 第二次跑撞 409                |
| `user_factory`                  | function     | 工厂：一条用例要造多个用户（对手账号）                 |
| `logged_user`                   | function     | 已登录用户 + 独立会话                        |
| `auth_headers`                  | function     | 字典是可变对象，且身份须独立                      |
| `a_sku`                         | function     | 商品/库存会被别的用例改，不能写死 sku_id            |
| `order_ready`                   | function     | 下单前置：加购 2 件 + 建地址                   |

**原则：贵的、不变的 → session；会变的、有唯一约束的、会被改的 → function。**

### 3. 身份隔离（订单组最容易踩的坑）

```python
api             # 干净会话，专门用来验证「未登录应 401」
logged_user     # 已登录用户，返回 {api, token, id, ...}
user_factory    # 想造几个用户就造几个（对手账号、越权场景）
```

**`logged_user` 必须自己开会话**，不能复用 `api`——否则 `api` 里残留 cookie，「测 Bearer 头」的用例实际走的是 cookie 路径，**绿了但测错了东西**。

### 4. 数据隔离优先，清理其次

购物车、订单、地址都是**用户私有数据**，`logged_user` 每次给新用户 → 天然隔离，不需要 `teardown` 打扫。

**唯一无法隔离的是库存**（全局共享资源），所以 `test_create_order_out_of_stock` 用 `try/finally` 自愈：

```
对手买光 19 件 → 库存 0 → 我方下单 40901 → 对手取消订单 → 库存回滚 19
```

实测跑前跑后库存均为 19，**净变化为零，可无限重复跑**。

### 5. 契约变更记录

| 时间         | 变更                                                  | 影响                                                                                                       |
| ---------- | --------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| 2026-11-01 | 凭证从「响应体 token」迁到 **httpOnly Cookie `access_token`** | 登录断言从 `data["token"]` 改为解析 `Set-Cookie`（HttpOnly / SameSite=lax / Max-Age）；响应体反向断言 `"token" not in data` |

服务端 `deps._extract_token`：**cookie 优先，其次兼容 `Authorization: Bearer`**（给脚本调试）。两条路径都有用例守着。

### 6. 已知缺陷（用 xfail 钉住）

```python
@pytest.mark.xfail(strict=True, reason="401 未附 WWW-Authenticate（RFC 7235 §3.1 MUST）")
def test_401_has_www_authenticate_header(api, base_url): ...
```

`strict=True`：**缺陷哪天被修好，用例会 XPASS 并判失败**，逼人回来删标记。缺陷修复必须有人知道，不能悄悄绿掉。

---

## 六、常见问题排查

| 现象                            | 原因                  | 处理                                                        |
| ----------------------------- | ------------------- | --------------------------------------------------------- |
| `502 upstream connect failed` | 系统代理劫持了 `127.0.0.1` | conftest 里已设 `trust_env=False`；手工验证加 `curl --noproxy '*'` |
| `ConnectionError 10061 积极拒绝`  | 服务没起 / 端口错          | `curl.exe http://127.0.0.1:8000/healthz` 先确认              |
| `40400 资源不存在`                 | 路径拼错（如漏 `/auth`）    | 打开 `/docs` 看路由地图                                          |
| Cookie 断言失败                   | 认证契约又变了             | 看响应头 `Set-Cookie`，再改 `conftest.COOKIE_NAME` 相关断言          |
| 用例莫名变红又变绿                     | 数据被别的用例污染           | 检查是否共用了 session 级 `api` 做登录                               |

---

## 七、命令速查

| 命令                         | 用途                    |
| -------------------------- | --------------------- |
| `pytest -q`                | 全量快速跑                 |
| `pytest -k order -v`       | 只跑名字含 order 的         |
| `pytest -x`                | 首条失败即停                |
| `pytest --setup-show`      | 看 fixture 建立/销毁顺序与作用域 |
| `pytest --collect-only -q` | 只列用例，不执行              |
| `pytest -s`                | 让 `print` 显示出来（调试用）   |

**退出码**：`0` 全过 / `1` 有失败 / `2` 中断 / `5` 没收集到用例（通常是 `-k` 拼错）。

---

## 八、后续计划

- [ ] 幂等键与库存的并发验证（多线程同时抢同一 SKU）
- [ ] 改密后 `token_version + 1` → 旧 cookie 立即失效（安全专项）
- [ ] CI：GitHub Actions 跑测试 + 上传 Allure artifact
- [ ] 迁移到测试平台（FastAPI + Vue3）：用例管理、触发执行、结果看板、报告链接
