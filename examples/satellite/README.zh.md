# Satellite Slot-Based Dynamic Link Prototype 中文说明

本文档对应当前主线的 slot-based dynamic link prototype，不再把旧的
candidate-link 模型作为长期主线。

## 当前原型范围

当前固定规模为：

- 3 个 `UT`
- 6 个 `ST`
- 3 个 `GW`
- 外加一套恢复后的 compile-time `B00 miniInternet`

其中 `ST` 分成两条轨道：

- `sat-a1`, `sat-a2`, `sat-a3`
- `sat-b1`, `sat-b2`, `sat-b3`

核心运行时控制都在
[controller.py](/home/seed/seed-emulator/examples/satellite/controller.py:1)。
SEED 只负责创建基础容器和固定轨内链路。
在当前版本中，SEED 也负责创建 compile-time 的地面 `miniInternet`
结构，包括 `IX100-IX105`、transit AS、stub AS，以及 `GW` 的地面 `IX`
上联接口。

## 当前阶段边界

当前阶段只做两件事：

- 恢复完整的 compile-time `B00 miniInternet`
- 保持 slot-based satellite runtime link model 可运行、可观察

当前阶段明确不做以下内容：

- 不要求 `UT` ping 通 `miniInternet`
- 不实现临时 `route/NAT`
- 不恢复旧的 `forwarding_state`、`candidate`、`snapshot` 主流程
- 不引入真实 `MPLS`
- satellite runtime side 不跑 `OSPF/iBGP/eBGP`

因此当前的 compile-time 地面互联网和 runtime 卫星动态链路层之间，
只建立“结构共存”的边界，不在这个阶段强行补回端到端转发。

## 当前链路模型

- `UT-ST`：每个 `UT` 预留 2 个 access slots
- `GW-ST`：每个 `GW` 预留 2 个 gateway slots
- cross-plane `ST-ST`：两条轨道共享 3 个可复用 slots
- intra-plane `ST-ST`：固定 `front/back` 链路，compile-time 创建并长期存在

每个 slot 对应一个 Docker bridge network。

- `UT` / `GW` 固定端在 `--init-slot-runtime` 阶段预连接
- `ST` 端在运行时动态 connect/disconnect
- `miniInternet` 内部链路和 `GW` 的地面 `IX` 上联是 compile-time 固定结构，不由 slot runtime 管理

## 当前状态文件

- `configs/topology.json`：静态 topology inventory，只描述 fixed links 和 slot inventory
- `configs/link_state.json`：controller 维护的运行时 slot/link state

旧的 `attach_state.json`、`topology_state.json`、`forwarding_state.json`
不再是当前原型的主状态面。

注意：

- `link_state.json` 仍然只记录 satellite slot runtime 和 `fixed_isl`
- 它不会记录 `miniInternet` 内部链路

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

## 启动方式

```bash
python3 examples/satellite/build_topology.py
cd examples/satellite/output
docker-compose up -d
python3 /home/seed/seed-emulator/examples/satellite/controller.py --init-slot-runtime
```

## 最小验证命令

查看当前 slot state：

```bash
python3 examples/satellite/controller.py --show-link-state
```

查看当前相关 Docker network 数量：

```bash
docker network ls --format '{{.Name}}' | grep -E '(access-slot|gateway-slot|cross-slot|^link-a|^link-b)'
```

查看某个容器里的 interface 数量：

```bash
docker exec ut1 ip -o link show | wc -l
docker exec gw1 ip -o link show | wc -l
docker exec sat-a1 ip -o link show | wc -l
```

触发一次 `UT-ST` 动态挂载：

```bash
python3 examples/satellite/controller.py --attach-ut ut1:sat-a1
python3 examples/satellite/controller.py --attach-ut ut1:sat-a1 --slot-name ut1-access-slot1
```

释放这个 `UT` slot：

```bash
python3 examples/satellite/controller.py --detach-ut ut1
```

触发一次 `GW-ST` 动态挂载：

```bash
python3 examples/satellite/controller.py --attach-gw gw1:sat-b1
python3 examples/satellite/controller.py --attach-gw gw1:sat-b1 --slot-name gw1-gateway-slot1
```

释放这个 `GW` slot：

```bash
python3 examples/satellite/controller.py --detach-gw gw1
```

触发一次 cross-plane 动态连接：

```bash
python3 examples/satellite/controller.py --connect-cross sat-a1:sat-b1:left:right
python3 examples/satellite/controller.py --connect-cross sat-a1:sat-b1:left:right --slot-name ab-cross-slot2
```

释放这个 cross-plane slot：

```bash
python3 examples/satellite/controller.py --release-slot ab-cross-slot0
```

查看某个容器里的接口/IP：

```bash
docker exec sat-a1 ip -o addr show
docker exec ut1 ip -o addr show
docker exec gw1 ip -o addr show
```

## Runtime Demo

当前提供一个串行 handover demo 脚本：

`examples/satellite/demo_runtime_handover.py`

示例：

```bash
python3 examples/satellite/demo_runtime_handover.py --interval 5 --iterations 2
```

这个 demo 会在以下两组状态之间切换：

- `t0`：`ut1 -> sat-a1`，`gw1 -> sat-b1`，cross-plane `sat-a1 <-> sat-b1`
- `t1`：先释放 cross-plane，再 detach `ut1`、detach `gw1`，然后 `ut1 -> sat-a2`，`gw1 -> sat-b2`，cross-plane `sat-a2 <-> sat-b2`

每一步串行 runtime 操作后，脚本都会打印：

- 当前 active links
- 当前 occupied slots
- 当前执行的 attach/detach/connect/release 操作
- `link_state.json` 摘要
- 当前 `UT` / `GW` / cross-plane access IP 的简单连通性探测结果

这个 demo 刻意复用同一组 slot：

- `ut1-access-slot0`
- `gw1-gateway-slot0`
- `ab-cross-slot0`

因此这里观察到的 probe IP 是“slot peer IP”，不是“某颗卫星专属 IP”。
也就是说，handover 时变化的是 `binding` 和容器 interface，而不是 probe IP 本身。

注意：这个当前 demo 只验证 slot runtime 的 attach/detach/connect/release、
slot reuse、状态一致性和可视化观察方式。它不表示“`UT -> miniInternet` 已经打通”，
也不引入临时 `route/NAT` 或旧 forwarding baseline。

## Controller 已实现的安全原则

- 每次 `docker network connect` 后，目标容器必须恰好新增 1 个 interface
- controller 只会 rename 它刚刚确认出来的新 interface
- 运行时验证失败时会回滚部分 connect
- 只有成功后才写 `link_state.json`
- cross-plane 连接采用“两端依次连接，第二端失败则回滚第一端”的策略
- 如果不指定 `--slot-name`，controller 会按 slot 名字排序后选择第一个空闲 slot

## Bootstrap 接口说明

- 某些容器中可能还能看到 SEED bootstrap 阶段留下的 `eth*` 接口，并且它们处于 `DOWN` 状态
- 这本身不代表 slot runtime 有 bug
- slot 校验关注的是 `docker network connect` 前后新增的 interface diff
- controller 关心的是“是否恰好新增了 1 个新接口”，而不是要求把所有历史 bootstrap `eth*` 都清空

## Routing 边界

- `miniInternet` 内部继续使用 SEED 的 `Routing + Ebgp + Ibgp + Ospf`
- `GW` 在地面 `IX` 上联侧继续使用 SEED 的 `eBGP` 语义
- `AS250` satellite side 和 `AS65000` 的卫星 runtime side 当前都明确从 `Ospf/Ibgp` 中屏蔽
- `UT-ST`、`GW-ST`、cross-plane `ST-ST`、以及当前固定轨内 `ISL`，仍由 controller 和 `link_state.json` 管理，不交给 SEED 自动计算卫星内部转发

## 观察建议

- `SEED map` URL：`http://127.0.0.1:8080/pro/map`
- 当前 map 应视为 Docker compiler 生成的编译期拓扑视图
- 在当前版本里，map 编译期视图应当能看到完整 `B00 miniInternet`、`AS250` satellite side、`AS65000` gateways
- 不要假设它会实时反映 runtime `docker network connect/disconnect` 的 slot 变化
- 运行时真相请以 demo 脚本输出和 `python3 examples/satellite/controller.py --show-link-state` 为准
- 当前阶段没有稳定的 `UT -> ground host` ping 目标，因为 forwarding/MPLS 故意不在范围内
- 这个 demo 复用同一个 `UT` access slot，所以推荐从 `UT` 侧持续 ping 同一个 slot peer IP，并同时观察 `link_state.json` 里的 `binding.satellite_id` 变化：

```bash
while true; do
  date
  docker exec ut1 sh -lc 'ping -c 1 -W 1 10.90.11.1 >/dev/null 2>&1 && echo ut1-slot0 up || echo ut1-slot0 down'
  python3 examples/satellite/controller.py --show-link-state | grep 'ut1-access-slot0'
  sleep 1
done
```

- 如果你想从 `GW` 侧观察 feeder handover，同样推荐持续 ping 同一个 slot peer IP，并观察 `binding.satellite_id`：

```bash
while true; do
  date
  docker exec gw1 sh -lc 'ping -c 1 -W 1 10.91.11.1 >/dev/null 2>&1 && echo gw1-slot0 up || echo gw1-slot0 down'
  python3 examples/satellite/controller.py --show-link-state | grep 'gw1-gateway-slot0'
  sleep 1
done
```

- 如果你想观察 cross-plane handover，可以从 `sat-a1` 或 `sat-a2` 侧看同一个 cross-plane peer IP 的短暂 down/up，并配合查看 `ab-cross-slot0` 的绑定变化：

```bash
while true; do
  date
  docker exec sat-a1 sh -lc 'ping -c 1 -W 1 10.92.1.2 >/dev/null 2>&1 && echo cross-slot0-from-a1 up || echo cross-slot0-from-a1 down'
  docker exec sat-a2 sh -lc 'ping -c 1 -W 1 10.92.1.2 >/dev/null 2>&1 && echo cross-slot0-from-a2 up || echo cross-slot0-from-a2 down'
  python3 examples/satellite/controller.py --show-link-state | grep 'ab-cross-slot0'
  sleep 1
done
```

- 这类观察的重点不是“IP 从 A 变成 B”，而是：
  - release/attach/connect 过程中 ping 可能短暂失败
  - 之后同一个 slot peer IP 会重新恢复连通
  - `link_state.json` 中的 `binding` 会从 `sat-a1/sat-b1` 切到 `sat-a2/sat-b2`

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

## 设计文档

详细设计见
[docs/satellite_phase1_design.zh.md](/home/seed/seed-emulator/examples/satellite/docs/satellite_phase1_design.zh.md:1)。
虽然文件名保留了历史命名，但内容已经改为描述当前的 Phase 2
slot-based prototype，并替代旧的 candidate-link 主叙事。
