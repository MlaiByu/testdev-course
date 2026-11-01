"""订单接口用例（API13-API16）

订单组跟前两组最大的区别：**有状态、有副作用**。
- 状态机：pending → paid / cancelled，非法流转回 40902
- 副作用：下单成功会扣库存、清空购物车 → 这是「事务真的成功了」的最强证据
- 金额一律字符串两位小数（"78.00"），不要转 float 比

可重复跑的根基是「一人一账号」的数据隔离（见 conftest 的 logged_user），
不是写 teardown 去打扫共享数据。
"""
import re
import uuid
from decimal import Decimal

import allure
import pytest

from conftest import DEFAULT_PASSWORD, make_phone

pytestmark = allure.feature("订单")


@allure.story("下单")
@allure.title("正常下单：pending + 金额正确 + 清空购物车 + 快照落库")
def test_create_order_success(order_ready, base_url):
    """API13 下单成功：pending + 金额正确 + 清空购物车 + 快照落库"""
    api = order_ready["api"]
    sku = order_ready["sku"]
    qty = order_ready["quantity"]

    # ① 动作：用加购项下单
    with allure.step("提交订单"):
        r = api.post(
            f"{base_url}/api/v1/orders",
            json={
                "address_id": order_ready["address_id"],
                "cart_item_ids": [order_ready["cart_item_id"]],
                "remark": "接口自动化下单",
            },
        )

    with allure.step("断言：状态码与业务码"):
        # ② 状态层
        assert r.status_code == 200, f"下单失败: {r.text}"
        body = r.json()
        assert body["code"] == 0

    with allure.step("断言：订单状态、单号格式、金额"):
        # ③ 业务字段层
        order = body["data"]
        assert order["status"] == "pending", "新订单必须是待支付"
        assert re.fullmatch(r"\d{20}", order["order_no"]), f"单号格式不对: {order['order_no']}"

        # 金额是字符串，由单价 × 数量推出，不是写死的常量。
        # 用 Decimal 而不是 float：0.1 + 0.2 != 0.3 那套误差在金额上是事故。
        expected = f"{Decimal(sku['price']) * qty:.2f}"
        assert order["total_amount"] == expected, f"金额应为 {expected}，实际 {order['total_amount']}"

    with allure.step("断言：下单后购物车被清空（事务提交的证据）"):
        # ④ 副作用层：下单成功必须清空购物车（事务真的提交了）
        cart = api.get(f"{base_url}/api/v1/cart").json()["data"]
        assert cart["total_quantity"] == 0, "下单后购物车没清空，事务或业务逻辑有问题"

    with allure.step("断言：订单详情是下单时的快照"):
        # ⑤ 快照层：订单详情里的商品是「下单当时的快照」，且时间线有第一条
        detail = api.get(f"{base_url}/api/v1/orders/{order['id']}").json()["data"]
        assert detail["status"] == "pending"
        assert len(detail["items"]) == 1
        assert detail["items"][0]["price"] == sku["price"], "快照价格与加购时不一致"
        assert detail["items"][0]["quantity"] == qty
        assert detail["timeline"][0]["label"] == "下单"
        assert detail["address_snapshot"]["receiver"] == "张三"


@allure.story("支付")
@allure.title("支付订单：pending → paid，时间线追加「支付成功」")
def test_pay_order_success(order_ready, base_url):
    """API14 支付订单：pending → paid，时间线追加「支付成功」"""
    api = order_ready["api"]
    order_id = _create_order(order_ready, base_url)

    r = api.post(f"{base_url}/api/v1/orders/{order_id}/pay")
    assert r.status_code == 200, f"支付失败: {r.text}"
    assert r.json()["data"]["status"] == "paid"

    detail = api.get(f"{base_url}/api/v1/orders/{order_id}").json()["data"]
    assert detail["status"] == "paid"
    assert [t["label"] for t in detail["timeline"]] == ["下单", "支付成功"]


@allure.story("状态机")
@allure.title("重复支付：40902 + 文案指明动作")
def test_pay_twice_conflict(order_ready, base_url):
    """API15 重复支付：40902，且文案指明动作（不是笼统的「操作失败」）

    重复支付返回 40902 是**正确行为**，前端不该当网络错误重试。
    """
    api = order_ready["api"]
    order_id = _create_order(order_ready, base_url)

    assert api.post(f"{base_url}/api/v1/orders/{order_id}/pay").status_code == 200

    # 再付一次
    r = api.post(f"{base_url}/api/v1/orders/{order_id}/pay")
    assert r.status_code == 409
    body = r.json()
    assert body["code"] == 40902
    assert body["message"] == "仅待支付订单可支付"


@allure.story("状态机")
@allure.title("取消已支付订单：40902 + 文案随动作变化")
def test_cancel_paid_order_conflict(order_ready, base_url):
    """API16 取消已支付订单：40902，文案是「可取消」而不是「可支付」

    同一个错误码、不同动作，文案不同——写死一句话就会红在这里。
    """
    api = order_ready["api"]
    order_id = _create_order(order_ready, base_url)
    assert api.post(f"{base_url}/api/v1/orders/{order_id}/pay").status_code == 200

    r = api.post(f"{base_url}/api/v1/orders/{order_id}/cancel")
    assert r.status_code == 409
    body = r.json()
    assert body["code"] == 40902
    assert body["message"] == "仅待支付订单可取消"

    # 状态未被污染
    assert api.get(f"{base_url}/api/v1/orders/{order_id}").json()["data"]["status"] == "paid"


@allure.story("库存")
@allure.title("库存不足：40901 + 整单回滚 + 购物车项保留")
def test_create_order_out_of_stock(order_ready, base_url, user_factory, api):
    """API17 库存不足：40901 + 整单回滚 + 购物车项保留（标记失效而非删除）

    难点在「怎么构造」：
    - 加购时传超库存量 → 在加购阶段就被 40901 拦掉，到不了下单
    - 只能让另一个会话先把库存买光，制造「加购时合法、下单时不足」

    库存是全局共享资源，无法靠「一人一账号」隔离，所以这条用例必须自愈：
    结束时让对手取消订单，库存按业务逻辑回滚，净变化为零，可无限重复跑。
    """
    sku = order_ready["sku"]
    api_self = order_ready["api"]

    stock = _current_stock(api, base_url, sku)
    if stock > 999:
        pytest.skip(f"库存 {stock} 件超过单次加购上限 999，无法用本手法构造")
    assert stock > 0, "SKU 已无库存，构造不出「本来够、后来不够」的场景"

    # ① 前置：对手把当前库存全部买走
    rival = user_factory("rival")
    added = rival["api"].post(
        f"{base_url}/api/v1/cart", json={"sku_id": sku["id"], "quantity": stock}
    )
    assert added.status_code == 200, f"对手加购失败: {added.text}"

    rival_address = _add_address(rival["api"], base_url)
    rival_order = rival["api"].post(
        f"{base_url}/api/v1/orders",
        json={"address_id": rival_address, "cart_item_ids": [added.json()["data"]["id"]]},
    )
    assert rival_order.status_code == 200, f"对手下单失败: {rival_order.text}"
    rival_order_id = rival_order.json()["data"]["id"]
    assert _current_stock(api, base_url, sku) == 0, "库存没被扣光，场景没建立"

    try:
        # ② 动作：我方下单，此时库存已为 0
        r = api_self.post(
            f"{base_url}/api/v1/orders",
            json={
                "address_id": order_ready["address_id"],
                "cart_item_ids": [order_ready["cart_item_id"]],
            },
        )

        # ③ 状态层 + 业务层
        assert r.status_code == 409, f"库存不足应返回 409，实际 {r.status_code}: {r.text}"
        body = r.json()
        assert body["code"] == 40901
        assert body["message"] == f"库存不足：{sku['sku_code']}"

        # ④ 回滚证据：购物车项还在（只是 is_valid=False），说明事务没提交
        cart = api_self.get(f"{base_url}/api/v1/cart").json()["data"]
        assert len(cart["items"]) == 1, "下单失败不该删购物车项"
        assert cart["items"][0]["is_valid"] is False, "库存为 0 时应标记失效"
        assert cart["total_quantity"] == 0, "total_quantity 只统计有效项，这里必须是 0"

        # ⑤ 未创建任何订单
        orders = api_self.get(f"{base_url}/api/v1/orders", params={"status": "all"}).json()["data"]
        assert orders["total"] == 0, "库存不足不该产生订单"
    finally:
        # ⑥ 自愈：对手取消订单，库存回滚（这是本用例能重复跑的关键）
        rival["api"].post(f"{base_url}/api/v1/orders/{rival_order_id}/cancel")

    assert _current_stock(api, base_url, sku) == stock, "取消订单后库存没有回滚"


@allure.story("越权")
@allure.title("越权访问他人订单：404 + 40400，且与「订单不存在」响应完全一致")
def test_other_users_order_is_invisible(order_ready, base_url, user_factory):
    """订单归属校验：不能用 403，也不能泄漏「这个订单存在」

    403 等于告诉攻击者「订单存在，只是不属于你」——他就能拿 id 递增去枚举
    全站订单。所以契约规定「不存在 or 不是你的」统一 40400。
    """
    mine = _create_order(order_ready, base_url)
    other = user_factory("other")

    # ① 别人的订单：必须 404
    r = other["api"].get(f"{base_url}/api/v1/orders/{mine}")
    assert r.status_code == 404, f"越权访问应 404，实际 {r.status_code}: {r.text}"
    assert r.json()["code"] == 40400

    # ② 关键：和「根本不存在的订单」响应一字不差，否则就是枚举探测器
    ghost = other["api"].get(f"{base_url}/api/v1/orders/99999999")
    assert ghost.status_code == r.status_code
    assert ghost.json()["code"] == r.json()["code"]
    assert ghost.json()["message"] == r.json()["message"], (
        "「别人的订单」和「不存在的订单」响应不同，可被用来枚举订单是否存在"
    )

    # ③ 越权尝试不许改动数据：订单还在，且还是 pending
    detail = order_ready["api"].get(f"{base_url}/api/v1/orders/{mine}").json()["data"]
    assert detail["status"] == "pending"


@allure.story("幂等")
@allure.title("幂等键：同 key 同 body 重放 → 返回同一订单，库存只扣一次")
def test_create_order_idempotent(order_ready, base_url):
    """网络超时后客户端重试，绝不能变成两个订单

    这是订单接口最容易被忽略、上线后最致命的机制：
    用户手抖点两下、网关重发、客户端超时重试——都会造成同一个请求发两次。
    没有幂等键，就是两个订单、两次扣库存、两次扣款。

    实测还有一个细节：第二次请求时购物车项已经被第一次下单删掉了，
    但仍返回 200 和同一订单 —— 说明幂等键的检查发生在购物车校验之前。
    """
    api = order_ready["api"]
    sku = order_ready["sku"]
    qty = order_ready["quantity"]
    idem_key = str(uuid.uuid4())
    headers = {"Idempotency-Key": idem_key}
    body = {
        "address_id": order_ready["address_id"],
        "cart_item_ids": [order_ready["cart_item_id"]],
    }

    stock_before = _current_stock(api, base_url, sku)

    with allure.step("第一次提交（正常下单）"):
        first = api.post(f"{base_url}/api/v1/orders", json=body, headers=headers)
        assert first.status_code == 200, f"首次下单失败: {first.text}"
        first_order = first.json()["data"]

    stock_after = _current_stock(api, base_url, sku)
    assert stock_after == stock_before - qty, "首次下单应扣减库存"

    with allure.step("用同一个幂等键重放（模拟客户端重试）"):
        replay = api.post(f"{base_url}/api/v1/orders", json=body, headers=headers)
        assert replay.status_code == 200, f"重放应成功返回原订单，实际 {replay.status_code}: {replay.text}"
        replay_order = replay.json()["data"]

    with allure.step("断言：同一个订单，不是新建的"):
        assert replay_order["id"] == first_order["id"], "重放创建了新订单，幂等失效"
        assert replay_order["order_no"] == first_order["order_no"]
        assert replay_order["total_amount"] == first_order["total_amount"]

    with allure.step("断言：库存没有被二次扣减"):
        assert _current_stock(api, base_url, sku) == stock_after, "重放导致重复扣库存"

    with allure.step("断言：库里只有一张订单"):
        orders = api.get(
            f"{base_url}/api/v1/orders", params={"status": "all"}
        ).json()["data"]
        assert orders["total"] == 1, f"应只有 1 张订单，实际 {orders['total']} 张"


@allure.story("幂等")
@allure.title("幂等键冲突：同 key 不同请求体 → 40903，且不产生第二张订单")
def test_idempotency_key_conflict(order_ready, base_url):
    """同一个 key 携带不同请求体，必须报 40903

    为什么不能「静默返回第一个订单」：那会掩盖客户端的 bug
    （比如复用了 key 但换了商品），用户以为下了 A 单，实际是 B 单。
    """
    api = order_ready["api"]
    sku = order_ready["sku"]
    idem_key = str(uuid.uuid4())
    headers = {"Idempotency-Key": idem_key}

    # ① 用 key 下第一单
    first = api.post(
        f"{base_url}/api/v1/orders",
        json={
            "address_id": order_ready["address_id"],
            "cart_item_ids": [order_ready["cart_item_id"]],
        },
        headers=headers,
    )
    assert first.status_code == 200, f"首次下单失败: {first.text}"

    # ② 再造一个购物车项，用同一个 key 提交不同请求体
    added = api.post(
        f"{base_url}/api/v1/cart", json={"sku_id": sku["id"], "quantity": 1}
    )
    assert added.status_code == 200, f"前置加购失败: {added.text}"

    r = api.post(
        f"{base_url}/api/v1/orders",
        json={
            "address_id": order_ready["address_id"],
            "cart_item_ids": [added.json()["data"]["id"]],
        },
        headers=headers,
    )

    # ③ 断言
    assert r.status_code == 409, f"同 key 不同 body 应 409，实际 {r.status_code}: {r.text}"
    body = r.json()
    assert body["code"] == 40903
    assert body["message"] == "相同幂等键携带了不同的请求"

    # ④ 冲突不能留下脏数据：订单仍只有一个，新建的购物车项还在
    orders = api.get(f"{base_url}/api/v1/orders", params={"status": "all"}).json()["data"]
    assert orders["total"] == 1, "幂等冲突不该产生新订单"
    cart = api.get(f"{base_url}/api/v1/cart").json()["data"]
    assert len(cart["items"]) == 1, "冲突后购物车项应保留，不能被清掉"


def _add_address(api_ctx, base_url):
    """建一个收货地址，返回 id"""
    r = api_ctx.post(
        f"{base_url}/api/v1/addresses",
        json={
            "receiver": "张三",
            "phone": make_phone(),
            "province": "广东省",
            "city": "深圳市",
            "district": "南山区",
            "detail": "科技园 1 号 A 座 101",
        },
    )
    assert r.status_code == 200, f"前置建地址失败: {r.text}"
    return r.json()["data"]["id"]


def _current_stock(api_ctx, base_url, sku):
    """回查 SKU 实时库存（库存会被其他用例改，不能用 fixture 里的旧值）"""
    detail = api_ctx.get(f"{base_url}/api/v1/products/{sku['product_id']}").json()["data"]
    return next(s["stock"] for s in detail["skus"] if s["id"] == sku["id"])


def _create_order(order_ready, base_url, **overrides):
    """下单动作的薄封装：只给本文件内复用，不进 conftest

    为什么不抽成 fixture：下单本身是被测动作，不是前置；
    有些用例要断言下单的失败（库存不足、幂等），必须能自己控制入参。
    """
    payload = {
        "address_id": order_ready["address_id"],
        "cart_item_ids": [order_ready["cart_item_id"]],
    }
    payload.update(overrides)
    r = order_ready["api"].post(f"{base_url}/api/v1/orders", json=payload)
    assert r.status_code == 200, f"前置下单失败，场景没建立: {r.text}"
    return r.json()["data"]["id"]
