"""登录接口用例（API08-API12）

断言层次：status_code → body.code → 业务字段 → 契约 → 凭证内容 → 活性
规矩：每条用例自给自足，不依赖别的用例先跑过。

认证契约（2026-11-01 变更）：
- 登录成功后凭证放在 httpOnly Cookie（access_token）里，响应体不再返回 token
- 服务端 _extract_token 优先 cookie，其次兼容 Authorization: Bearer（供脚本调试）
- 所以「登录成功」的断言也从「取 token」变成「验 Set-Cookie + 验 cookie 能用」
"""
import allure

from conftest import (
    COOKIE_NAME,
    DEFAULT_PASSWORD,
    cookie_of,
    decode_jwt,
    make_email,
    new_session,
)

pytestmark = allure.feature("认证")


@allure.story("登录")
@allure.title("正常登录：200 + httpOnly cookie + 凭证内容正确 + cookie 可用")
def test_login_success(api, base_url, unique_email):
    """API08 正常登录：200 + 下发 httpOnly cookie + 凭证内容正确 + cookie 可用"""
    # ① 前置：注册一个我掌握密码的账号（并证明场景已抵达）
    reg = api.post(
        f"{base_url}/api/v1/auth/register",
        json={"email": unique_email, "password": DEFAULT_PASSWORD},
    )
    assert reg.status_code == 201, f"前置注册失败，场景没建立: {reg.text}"

    # ② 动作：同一个邮箱 + 正确的密码（注意字段名是 account，不是 email）
    r = api.post(
        f"{base_url}/api/v1/auth/login",
        json={"account": unique_email, "password": DEFAULT_PASSWORD},
    )

    # ③ 状态层
    assert r.status_code == 200
    # ④ 业务层
    body = r.json()
    assert body["code"] == 0

    # ⑤ 业务字段层
    assert body["data"]["expires_in"] == 86400
    user = body["data"]["user"]
    assert user["email"] == unique_email
    assert user["role"] == "customer"
    assert user["is_admin"] is False
    assert "hashed_password" not in user

    # ⑥ 契约层：凭证不得出现在响应体（本次变更的核心）
    assert "token" not in body["data"]

    # ⑦ Cookie 契约层：凭证必须在 httpOnly cookie 里（按属性断言，不靠字符串匹配）
    c = cookie_of(r)
    assert c["httponly"] is True, "缺 HttpOnly：JS 可读，XSS 能偷走凭证"
    assert c["samesite"] == "lax", "缺 SameSite=lax：跨站请求会带上凭证，有 CSRF 风险"
    assert c["max-age"] == "86400", "有效期与 expires_in 不一致"
    assert c.value.count(".") == 2, "cookie 里的凭证不是 JWT 三段结构"

    # ⑧ 凭证内容层：JWT payload 是明文，既要验内容，也要验「没多塞东西」
    jwt = decode_jwt(c.value)
    assert jwt["header"]["alg"] == "HS256", "算法被降级：alg=none 伪造会绕过验签"
    assert jwt["payload"]["sub"] == str(user["id"]), "凭证绑定的身份与登录用户不一致"
    assert jwt["payload"]["is_admin"] is False, "普通用户凭证里不许出现管理员标记"
    assert jwt["payload"]["exp"] - jwt["payload"]["iat"] == 86400, "有效期与 expires_in 不一致"
    for field in ("email", "phone", "password", "hashed_password"):
        assert field not in jwt["payload"], f"敏感字段 {field} 泄漏进了 JWT payload"

    # ⑨ 活性层：cookie 由 Session 自动保管并回传，这才证明凭证可用
    me = api.get(f"{base_url}/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["data"]["email"] == unique_email


@allure.story("登录")
@allure.title("密码错误：401 + 40100 + 与「账号不存在」一字不差的文案")
def test_login_wrong_password(api, base_url, unique_email):
    """API09 密码错误：401 + 40100 + 与「账号不存在」一字不差的文案（反枚举）"""
    # ① 前置：账号必须真实存在，否则测的是「账号不存在」而不是「密码错误」
    reg = api.post(
        f"{base_url}/api/v1/auth/register",
        json={"email": unique_email, "password": DEFAULT_PASSWORD},
    )
    assert reg.status_code == 201, f"前置注册失败，场景没建立: {reg.text}"

    # ② 动作：同一个邮箱 + 格式合法但内容错误的口令
    r = api.post(
        f"{base_url}/api/v1/auth/login",
        json={"account": unique_email, "password": "WrongPass1"},
    )

    # ③ 状态层：必须是 401（404 会泄漏用户不存在，403 是已登录但无权限）
    assert r.status_code == 401
    # ④ 业务层
    body = r.json()
    assert body["code"] == 40100
    # ⑤ 安全层：文案必须与「账号不存在」完全一致，否则可批量探测邮箱是否注册
    assert body["message"] == "账号或密码错误"
    assert body["data"] is None
    # ⑥ 契约层：失败时也不许下发凭证
    assert COOKIE_NAME not in r.cookies


@allure.story("登录")
@allure.title("账号不存在：401 + 40100（前置什么都不做，验证系统不多嘴）")
def test_login_account_not_exist(api, base_url):
    """API10 账号不存在：401 + 40100 + 与「密码错误」一字不差的文案（反枚举）

    前置是「什么都不做」——被注册过的账号才叫前置，
    这条测的正是「用户不存在时系统会不会多嘴」。
    """
    ghost = make_email("ghost")          # 造一个绝不会被注册的邮箱
    r = api.post(
        f"{base_url}/api/v1/auth/login",
        json={"account": ghost, "password": DEFAULT_PASSWORD},
    )
    # 状态层：必须是 401，不能是 404 —— 状态码本身也能泄漏「用户不存在」
    assert r.status_code == 401
    body = r.json()
    assert body["code"] == 40100
    assert body["message"] == "账号或密码错误"
    assert body["data"] is None


@allure.story("鉴权")
@allure.title("Bearer 头兼容路径：服务端 _extract_token 支持，防止被误删")
def test_auth_header_compat(api, base_url, auth_headers):
    """API11 Bearer 头兼容路径：服务端 _extract_token 明确支持，防止被误删

    这条必须用「没有 cookie 的会话」+ Bearer 头，否则服务端 cookie 优先，
    测到的就是 cookie 路径而不是头路径——绿了但测错了东西。
    api 是 function 级的干净会话，auth_headers 来自另一个独立会话，刚好隔离。
    """
    r = api.get(f"{base_url}/api/v1/auth/me", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["code"] == 0


@allure.story("登出")
@allure.title("退出登录：清除 cookie，之后访问受保护接口回到未登录态")
def test_logout_clears_cookie(logged_user, base_url):
    """API12 退出登录：清除 cookie，之后访问受保护接口回到未登录态

    前置交给 logged_user fixture：注册+登录 6 行重复代码不用再抄一遍，
    但断言全部留在用例里——前置可以抽象，断言不行。
    """
    api = logged_user["api"]              # 已带 cookie 的会话
    assert api.get(f"{base_url}/api/v1/auth/me").status_code == 200

    # ① 动作：退出
    r = api.post(f"{base_url}/api/v1/auth/logout")
    assert r.status_code == 200
    assert r.json()["code"] == 0

    # ② 契约层：用 Max-Age=0 + 空值 让客户端删掉 cookie
    c = cookie_of(r)
    assert c["max-age"] == "0", "退出登录没有让 cookie 立即过期"
    assert c.value == "", "退出登录没有把 cookie 值清空"

    # ③ 活性层：退出后必须回到未登录态（这条才真正验证「凭证已失效」）
    me = api.get(f"{base_url}/api/v1/auth/me")
    assert me.status_code == 401
    assert me.json()["code"] == 40100


@allure.story("凭证失效")
@allure.title("改密后旧 cookie 立即失效（token_version 机制）")
def test_change_password_invalidates_old_token(logged_user, base_url):
    """改密后已签发的凭证必须立即作废

    背景：JWT 天生无状态，签出去就收不回来，没法像 session 那样「删掉服务端记录」。
    项目用 token_version 解决：用户表存一个版本号，签发时写进 payload，
    改密/封号时版本号 +1，校验时比对不上就拒 —— 相当于「一键踢下线」。

    这条用例同时守住三件事，缺一件都可能漏 bug：
    ① 旧凭证在同一个会话里失效
    ② 旧凭证换一个全新会话也失效（证明是 token 作废，不是 session 被清了 cookie）
    ③ 旧密码登不进、新密码能登进（证明密码真的改了）
    """
    me_url = f"{base_url}/api/v1/auth/me"
    old_token = logged_user["token"]
    old_password = logged_user["password"]
    new_password = "NewPass123"

    # ① 前置：改密前旧凭证可用（场景抵达证明）
    with allure.step("前置：确认旧凭证当前可用"):
        assert logged_user["api"].get(me_url).status_code == 200

    # ② 动作：改密
    with allure.step("修改密码"):
        r = logged_user["api"].post(
            f"{base_url}/api/v1/auth/change-password",
            json={"old_password": old_password, "new_password": new_password},
        )
        assert r.status_code == 200, f"改密失败: {r.text}"
        assert r.json()["code"] == 0

    with allure.step("断言：同一会话里的旧凭证立即失效"):
        me = logged_user["api"].get(me_url)
        assert me.status_code == 401, "改密后旧凭证还能用，token_version 没生效"
        assert me.json()["code"] == 40101
        # 40101 有三种文案，改密场景是「已失效」，前端据此提示「请重新登录」
        assert me.json()["message"] == "登录凭证已失效，请重新登录"

    with allure.step("断言：旧凭证换一个全新会话同样无效"):
        # 关键：同一个 session 失效可能只是因为服务端清了 cookie，
        # 换个干净会话手动塞进去还能用的话，等于凭证根本没作废
        ghost = new_session()
        try:
            ghost.cookies.set(COOKIE_NAME, old_token)
            replay = ghost.get(me_url)
            assert replay.status_code == 401, "旧凭证在别的会话里仍然可用，等于没作废"
            assert replay.json()["code"] == 40101
        finally:
            ghost.close()

    with allure.step("断言：旧密码登不进，新密码能登进"):
        old_login = logged_user["api"].post(
            f"{base_url}/api/v1/auth/login",
            json={"account": logged_user["email"], "password": old_password},
        )
        assert old_login.status_code == 401, "旧密码还能登录，密码没真正改掉"

        new_login = logged_user["api"].post(
            f"{base_url}/api/v1/auth/login",
            json={"account": logged_user["email"], "password": new_password},
        )
        assert new_login.status_code == 200, f"新密码登录失败: {new_login.text}"
