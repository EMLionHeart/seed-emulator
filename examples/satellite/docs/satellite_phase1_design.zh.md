# Satellite Phase 2 Slot-Based Dynamic Link Design 中文说明

英文版：
[satellite_phase1_design.md](/home/seed/seed-emulator/examples/satellite/docs/satellite_phase1_design.md:1)

本文档描述当前主线的 satellite phase2 slot-based dynamic link prototype。
旧的 candidate-link / snapshot activation 主线在这个阶段不再继续保留为主叙事。

## 范围

- 主要工作范围保持在 `examples/satellite/`
- 默认不改 SEED core
- 规模固定为 3 `UT`、6 `ST`、3 `GW`
- 恢复完整的 `B00 miniInternet` 作为地面互联网结构
- 当前只验证 slot-based runtime link model
- 不要求真实 `MPLS`、大规模扩展或自定义前端

当前阶段明确不做以下内容：

- 不要求 `UT` ping 通 `miniInternet`
- 不实现临时 `route/NAT`
- 不恢复旧的 `forwarding_state`、`candidate`、`snapshot` 主流程
- 不引入真实 `MPLS`
- 不让 satellite runtime side 跑 `OSPF/iBGP/eBGP`

## 运行时边界

- SEED 负责创建基础容器、fixed intra-plane links，以及 compile-time `miniInternet`
- `controller.py` 负责 runtime slot bridge 创建与 connect/disconnect
- 所有 runtime state 都显式记录在 `link_state.json`
- 不依赖隐藏的后台状态机
- `link_state.json` 仍然只记录 satellite slot runtime 和 `fixed_isl`
- `miniInternet` 内部链路不会写进 `link_state.json`

## UT 地址语义

当前原型明确区分两类地址：

- `UT stable identity address/prefix`
  - `ut1 = 100.64.0.1/32`
  - `ut2 = 100.64.0.2/32`
  - `ut3 = 100.64.0.3/32`
  - 这些地址挂在 `dummy0` 上，在 `UT-ST` attach/detach 过程中保持不变
  - 后续 forwarding/MPLS 阶段应把它们视为业务 endpoint / `FEC` / 用户身份
- `UT-ST runtime slot link address`
  - 例如 `10.90.x.x/29`
  - 这些地址只表示当前 access link 上的链路下一跳
  - 不要把 slot link IP 当作用户身份地址

## Routing 边界

- 恢复后的 `miniInternet` 内部继续使用 SEED 的 `Routing + Ebgp + Ibgp + Ospf`
- `GW` 的地面 `IX` 上联继续使用 SEED 的 `eBGP`
- `AS250` 和 `AS65000` 的卫星 runtime side 明确从 SEED `Ospf/Ibgp` 中屏蔽
- `UT-ST`、`GW-ST`、cross-plane `ST-ST`，以及当前固定轨内 `ISL`，仍然由 controller 和 `link_state.json` 管理

## 链路模型

### `UT-ST`

- 每个 `UT` 预留 2 个 access slots
- 每个 slot 对应 1 个 Docker bridge network
- 初始化阶段只连接 `UT` 端
- 运行时动态连接或断开 1 个 `ST`
- 如果没有空闲 slot，controller 必须清晰失败

### `GW-ST`

- 每个 `GW` 预留 2 个 gateway slots
- 每个 slot 对应 1 个 Docker bridge network
- 初始化阶段只连接 `GW` 端
- 运行时动态连接或断开 1 个 `ST`
- 如果没有空闲 slot，controller 必须清晰失败

### 固定轨内 `ISL`

- `sat-a1 <-> sat-a2 <-> sat-a3`
- `sat-b1 <-> sat-b2 <-> sat-b3`
- 这些链路保持 compile-time fixed p2p links
- 它们会以 `fixed_isl` 形式出现在 `link_state.json`

### 跨轨 `ISL`

- orbit-a 和 orbit-b 共享 3 个可复用 cross-plane slots
- 每个 slot 对应 1 个共享 Docker bridge network
- 初始化阶段只创建 bridge，不连接任何 `ST`
- 运行时把两颗 `ST` 同时连接到同一个 slot bridge
- 断开时移除两端，slot 回到空闲状态

## 状态模型

`link_state.json` 是 controller 拥有的 runtime state 文件。

每个 slot entry 记录：

- `slot_name`
- `slot_type`
- `network_name`
- `docker_network_name`
- `subnet`
- `bridge_gateway_ip`
- `preconnected_endpoints`
- `dynamic_endpoints`
- `occupied`
- `binding`

对于 cross-plane slot，`binding` 记录：

- `satellite_a_id`
- `satellite_b_id`
- `satellite_a_direction`
- `satellite_b_direction`

对于固定轨内链路，`slot_type=fixed_isl` 且 `occupied=true`。

## 安全原则

- 每次 `docker network connect` 后，目标容器必须恰好新增 1 个 interface
- 如果新增数量不是 1，立即 abort
- 只允许 rename 刚刚确认出的新 interface
- 不 rename 任何不确定的已有 interface
- 失败时不写 `link_state.json`
- cross-plane 第二端连接失败时，必须回滚第一端

## 当前 CLI

初始化所有 runtime slot bridges 和固定端预连接：

```bash
python3 examples/satellite/controller.py --init-slot-runtime
```

查看 runtime state：

```bash
python3 examples/satellite/controller.py --show-link-state
```

连接和释放 `UT`：

```bash
python3 examples/satellite/controller.py --attach-ut ut1:sat-a1
python3 examples/satellite/controller.py --attach-ut ut1:sat-a1 --slot-name ut1-access-slot1
python3 examples/satellite/controller.py --detach-ut ut1
```

连接和释放 `GW`：

```bash
python3 examples/satellite/controller.py --attach-gw gw1:sat-b1
python3 examples/satellite/controller.py --attach-gw gw1:sat-b1 --slot-name gw1-gateway-slot1
python3 examples/satellite/controller.py --detach-gw gw1
```

连接和释放 cross-plane `ISL`：

```bash
python3 examples/satellite/controller.py --connect-cross sat-a1:sat-b1:left:right
python3 examples/satellite/controller.py --connect-cross sat-a1:sat-b1:left:right --slot-name ab-cross-slot2
python3 examples/satellite/controller.py --release-slot ab-cross-slot0
```

如果 attach/connect 时不指定 `--slot-name`，controller 会按 slot 名字排序后选择第一个空闲 slot。

## Bootstrap 接口注意事项

- 某些容器里可能还保留着 SEED bootstrap 阶段留下的 `eth*` 接口，并且状态是 `DOWN`
- 这本身不被视为 slot runtime bug
- controller 的 slot 安全校验方式，是比较 `docker network connect` 前后的 interface 集合差异
- 成功条件是“恰好新增 1 个新接口”，而不是“所有旧 bootstrap `eth*` 都必须消失”

## 当前验证目标

当前 prototype 只需要证明以下几点：

- slot 资源是有限且可见的
- runtime bridge 可以复用
- controller-visible state 一致
- 局部失败时可以安全回滚
- 通过 Docker 和 `link_state.json` 容易观察与调试

## Runtime Demo 循环

当前 demo 循环刻意保持小而串行：

- `t0`
  - `ut1 -> sat-a1`
  - `gw1 -> sat-b1`
  - cross-plane `sat-a1 <-> sat-b1`
- `t1`
  - release cross-plane
  - detach `ut1`
  - detach `gw1`
  - `ut1 -> sat-a2`
  - `gw1 -> sat-b2`
  - cross-plane `sat-a2 <-> sat-b2`

对应的 demo 驱动脚本是：

`examples/satellite/demo_runtime_handover.py`

它只做串行 runtime 操作，并打印：

- 当前 active links
- 当前 occupied slots
- 当前 runtime 操作
- `link_state.json` 摘要
- 当前状态下的简单连通性探测结果

这个 demo 刻意复用同一组 slot：

- `ut1-access-slot0`
- `gw1-gateway-slot0`
- `ab-cross-slot0`

因此 probe IP 是 slot peer IP，而不是某颗卫星独占的 access/feeder/cross-plane IP。
handover 过程中主要变化的是 `binding`、容器 interface 和瞬时连通性，而不是 probe IP 本身。

注意：这个当前 demo 只验证 slot runtime 的动态链路行为和状态一致性，
不表示“`UT -> miniInternet` 已经打通”，也不引入临时 `route/NAT` 或旧 forwarding baseline。

## Map 观察边界

- 当前示例的 `SEED map` 地址是 `http://127.0.0.1:8080/pro/map`
- 在这个阶段，应把它视为编译期拓扑可视化
- 当前这个编译期视图应当能看到恢复后的完整 `B00 miniInternet`、`AS250` satellite side，以及 `AS65000` gateways
- 不要把它当作 runtime slot bridge connect/disconnect 的实时渲染结果
- 运行时真相仍然以 controller 输出、容器接口状态和 `link_state.json` 为准
- 由于当前阶段故意不做 forwarding/MPLS，所以还没有稳定的 `UT -> ground host` 端到端探测目标
- 建议的观察方式是：
  - 从 `UT` 持续 ping `10.90.11.1`，并同时观察 `ut1-access-slot0` 的 `binding.satellite_id`
  - 从 `GW` 持续 ping `10.91.11.1`，并同时观察 `gw1-gateway-slot0` 的 `binding.satellite_id`
  - 从 `sat-a1` 或 `sat-a2` 观察 cross-plane peer `10.92.1.2` 的短暂 down/up，并同时观察 `ab-cross-slot0` 的绑定变化
- 当前 demo 的重点是：
  - runtime slot stability
  - dynamic link visualization
  - rollback safety
  - slot reuse
  - Docker network/interface cleanup

## Future Forwarding/MPLS Demo Plan

下面这组状态设计是后续 forwarding/MPLS baseline 阶段准备采用的循环切换方案。
它目前只是 plan，不代表当前已经实现 `UT -> miniInternet` 连通性。

`t0`:

- `ut1 -> sat-a1`
- `ut2 -> sat-a2`
- `gw1 -> sat-b1`
- `gw2 -> sat-a3`
- `ab-cross-slot0: sat-a1(left) <-> sat-b1(right)`
- `ab-cross-slot1: sat-a2(left) <-> sat-b2(right)`

`t1`:

- `ut1 -> sat-a2`
- `ut2 -> sat-a3`
- `gw1 release`
- `gw2 -> sat-b2`
- `ab-cross-slot0: sat-a2(left) <-> sat-b2(right)`
- `ab-cross-slot1: sat-a3(left) <-> sat-b3(right)`

后续 forwarding/MPLS 阶段的目标，是让 `UT stable identity address/prefix`
通过这些动态链路状态变化，仍然能够到达 `miniInternet` 中的 external host。
但这不属于当前阶段的交付范围。
