#!/usr/bin/env python3
import os
import aws_cdk as cdk
from live_streaming_demo.live_streaming_demo_stack import LiveStreamingDemoStack

app = cdk.App()

# 默认法兰克福 (eu-central-1, 三服务齐全); 可用 -c region=xx 覆盖。
# 注意: 目标 region 必须同时支持 MediaLive / MediaPackage / IVS, 否则 IVS 创建失败会回滚整个 stack。
region = app.node.try_get_context("region") or "eu-central-1"
account = os.environ.get("CDK_DEFAULT_ACCOUNT")

# 推流端放行 CIDR (demo 默认全放开; 生产请收敛到真实出口 IP)
input_cidr = app.node.try_get_context("input_cidr") or "0.0.0.0/0"

LiveStreamingDemoStack(
    app, "LiveStreamingDemo",
    input_cidr=input_cidr,
    env=cdk.Environment(account=account, region=region),
    description="Live streaming three-way comparison demo (HLS + LL-HLS + IVS) with player site",
)

app.synth()
