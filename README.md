# 直播三方式对比 Demo — CDK (默认 Frankfurt / eu-central-1)

把直播三路对比 demo 收敛成**一个 CDK stack**,三路同源对比 + 前端页面一起托管:

| 路 | 链路 | 延迟量级 | 两头自管 |
|----|------|---------|---------|
| ① 标准 HLS | MediaLive → MediaPackage(HLS 6s) → CloudFront | ~20–30s | ✅ |
| ② LL-HLS | MediaLive(GOP=1) → MediaPackage(CMAF 1s) → CloudFront | ~5–10s | ✅ |
| ③ Amazon IVS | IVS 端到端(RTMPS 443) | ~3s | ❌ 绑定 IVS |

编码参数(5 档 ABR / QVBR / GOP=1s)还原自 "Live Streaming on AWS" (SO0013)
参考部署的 MediaLive 频道,见 `live_streaming_demo/encoder_settings.py`。

## 架构

```
ffmpeg(含时间戳水印) ─RTMP 1935─► MediaLive ─► MediaPackage ┬─ HLS  6s ─┐
                                  (STANDARD)               └─ CMAF 1s ─┴─► CloudFront(+CORS) ─► 浏览器 ①②
ffmpeg ─────────────RTMPS 443─► Amazon IVS ───────────────────────────────► IVS 全球边缘 ─► 浏览器 ③

前端页面: S3(私有+OAC) ─► CloudFront ; config.json 由自定义资源 Lambda 部署后写入真实地址
```

## 目录

```
.
├── app.py                                  CDK 入口
├── cdk.json
├── requirements.txt
├── live_streaming_demo/
│   ├── live_streaming_demo_stack.py        主 stack
│   └── encoder_settings.py                 MediaLive 编码参数(实测还原)
├── lambda/config_writer/index.py           自定义资源: 写 config.json
└── web/index.html                          前端对比页(运行时读 config.json)
```

## 部署

前提:已配置指向目标账户的 AWS 凭证(`aws sts get-caller-identity` 能通);装好 Node + AWS CDK v2、Python3。**无需 Docker**(静态资源上传与自定义资源 Lambda 均未开 bundling)。

```bash
cd <项目目录>                          # clone 下来的仓库目录
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cdk bootstrap                          # 该账户/region 首次执行一次
cdk deploy --require-approval never    # 约 5–20 分钟(MediaLive 创建较慢)
```

> `cdk deploy` 默认会**弹一次** IAM 安全确认(`Do you wish to deploy these changes (y/n)?`,输入 `y`)。
> 加 `--require-approval never` 可跳过该提示、无人值守一路跑完。

可选参数:
- `cdk deploy -c region=<region>` 换区域(默认 `eu-central-1` 法兰克福)
- `cdk deploy -c input_cidr=<你的出口IP>/32` 收敛推流放行(默认 0.0.0.0/0,仅 demo)

> ⚠️ **目标 region 必须同时支持 MediaLive + MediaPackage + IVS**,否则 IVS 频道创建失败会回滚整个 stack。
> 已确认全支持(可三路):eu-central-1(默认)、eu-west-1、us-east-1、us-west-2、ap-northeast-1、ap-south-1。
> ❌ 不支持:中国区(cn-north-1/cn-northwest-1)、香港(ap-east-1);⚠️ 新加坡(ap-southeast-1)无 IVS。

### 部署完会输出什么 / 接下来做什么

`cdk deploy` 结束会打印 `Outputs:` 区块,**每条的值就是可直接复制执行的命令或地址**,按 ①→⑤ 照做即可:

```
Step1OpenTestPage = https://xxxx.cloudfront.net/                                  ← 打开它 = 三路对比测试页
Step2StartChannel = aws medialive start-channel --channel-id 1234 --region eu-...  ← 复制执行: 启动(开始计费)
Step3PushStream   = https://xxxx.cloudfront.net/                                   ← 测试页点"复制"拿 ffmpeg 命令推流
Step4StopChannel  = aws medialive stop-channel  --channel-id 1234 --region eu-...  ← 复制执行: 演示完停止(停止计费)
Step5Cleanup      = cdk destroy                                                    ← 不再使用 = 删全部
```

> 🔑 **`cdk deploy` 只建资源、不自动启动**:MediaLive 频道创建后停在 **IDLE(不计费)**。
> 要看到 ①② 画面,必须先跑 **Step2** 启动频道;③ IVS 无需启动,部署完直接推流就有画面。

## 启动 / 推流 / 停止

```bash
# 1. 启动 MediaLive 频道(产生费用)
aws medialive start-channel --channel-id <MediaLiveChannelId> --region eu-central-1

# 2. 打开 PlayerSiteURL, 点页面里的"复制"拿推流命令, 终端运行
#    ① + ② 推 MediaLive(RTMP 1935) ; ③ 推 IVS(RTMPS 443)

# 3. 演示完停频道(省钱)
aws medialive stop-channel --channel-id <MediaLiveChannelId> --region eu-central-1
```

> ⚠️ 公司网常封 1935 端口 → MediaLive 推流超时。IVS 走 443 通常可推;或换手机热点推 MediaLive。

## 清理

```bash
aws medialive stop-channel --channel-id <MediaLiveChannelId> --region eu-central-1   # 先停
cdk destroy                                                                            # 删全部
```

`cdk destroy` 会一并删除 MediaLive / MediaPackage / IVS / 两个 CloudFront / S3(已开 auto-delete)。

## 设计说明 / 注意

- **config.json 为何用 Lambda 写**:MediaPackage 端点播放 URL 含自动生成 GUID,synth 阶段拿不到,部署后由自定义资源 `DescribeOriginEndpoint` 读真实 URL,再把 host 换成 CloudFront 域名写入网站桶。
- **CloudFront 缓存策略**:`*.m3u8` 走 `CACHING_DISABLED`(manifest 实时),其余分片走 `CACHING_OPTIMIZED`,符合直播分发最佳实践。
- **CloudFront 是分发加速,不降延迟**:延迟由分片大小 + 播放器缓冲决定;要 3s 级低延迟 + 开箱即用选 IVS。
- **未照搬的部分**:现网官方 stack(SO0013)还带 DASH 端点和 demo 播放器、S3 日志桶等,本 CDK 只保留对比 demo 实际用到的 HLS / LL-HLS / IVS 三路,更精简。
- 本工程为 AI 生成,**部署前请 `cdk synth` 校验**;媒体类 L1 资源属性较多,以实际 synth/deploy 结果为准。
