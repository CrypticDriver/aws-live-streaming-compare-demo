"""
Live Streaming Demo — 单一 CDK Stack

把直播三路对比 demo 收敛到一个 stack:
  ① 标准 HLS   : MediaLive -> MediaPackage(HLS 6s)  -> CloudFront(+CORS)
  ② LL-HLS     : MediaLive -> MediaPackage(CMAF 1s) -> CloudFront(+CORS)
  ③ Amazon IVS : 独立低延迟频道 (RTMPS 443, 端到端)
  + 前端对比页面 : S3 + CloudFront 托管, 运行时读 config.json
  + config.json : 部署后由自定义资源 Lambda 写入真实播放/推流地址

参考来源: "Live Streaming on AWS" (SO0013) 参考部署 + 手动添加的 LL-HLS 端点。
"""
from pathlib import Path

from aws_cdk import (
    Stack,
    CfnOutput,
    RemovalPolicy,
    Duration,
    CustomResource,
    aws_iam as iam,
    aws_medialive as medialive,
    aws_mediapackage as mediapackage,
    aws_ivs as ivs,
    aws_s3 as s3,
    aws_s3_deployment as s3deploy,
    aws_cloudfront as cf,
    aws_cloudfront_origins as origins,
    aws_lambda as lambda_,
    custom_resources as cr,
    Fn,
)
from constructs import Construct

from .encoder_settings import ENCODER_SETTINGS

PROJECT = "live-streaming-demo"
HLS_ENDPOINT_ID = f"{PROJECT}-hls"   # 标准 HLS, 6s 分片
LL_ENDPOINT_ID = f"{PROJECT}-llhls"  # 低延迟 LL-HLS, 1s 分片
CHANNEL_ID = f"{PROJECT}-livestream"


class LiveStreamingDemoStack(Stack):
    def __init__(self, scope: Construct, cid: str, *, input_cidr: str = "0.0.0.0/0", **kw):
        super().__init__(scope, cid, **kw)

        # ─────────────────────────────────────────────────────────────
        # 1) MediaLive — RTMP_PUSH 输入 + 输入安全组
        # ─────────────────────────────────────────────────────────────
        input_sg = medialive.CfnInputSecurityGroup(
            self, "InputSG",
            whitelist_rules=[medialive.CfnInputSecurityGroup.InputWhitelistRuleCidrProperty(cidr=input_cidr)],
        )

        ml_input = medialive.CfnInput(
            self, "Input",
            name=f"{PROJECT}-input",
            type="RTMP_PUSH",
            input_security_groups=[input_sg.ref],
            destinations=[
                medialive.CfnInput.InputDestinationRequestProperty(stream_name=f"{PROJECT}/primary"),
                medialive.CfnInput.InputDestinationRequestProperty(stream_name=f"{PROJECT}/secondary"),
            ],
        )

        # MediaLive 执行角色 (推流到 MediaPackage)
        ml_role = iam.Role(
            self, "MediaLiveRole",
            assumed_by=iam.ServicePrincipal("medialive.amazonaws.com"),
            inline_policies={
                "mediapackage": iam.PolicyDocument(statements=[
                    iam.PolicyStatement(actions=[
                        "mediapackage:DescribeChannel",
                        "mediaconnect:ManagedDescribeFlow",
                        "mediaconnect:ManagedAddOutput",
                        "mediaconnect:ManagedRemoveOutput",
                    ], resources=["*"]),
                    iam.PolicyStatement(actions=[
                        "logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams",
                    ], resources=["arn:aws:logs:*:*:*"]),
                ])
            },
        )

        # ─────────────────────────────────────────────────────────────
        # 2) MediaPackage — 1 channel + 2 origin endpoints
        # ─────────────────────────────────────────────────────────────
        mp_channel = mediapackage.CfnChannel(self, "MpChannel", id=CHANNEL_ID,
                                              description="Live streaming demo")

        # ② 标准 HLS endpoint — 6s 分片 (高延迟基线)
        hls_ep = mediapackage.CfnOriginEndpoint(
            self, "HlsEndpoint",
            id=HLS_ENDPOINT_ID,
            channel_id=mp_channel.id,
            manifest_name="index",
            startover_window_seconds=0,
            hls_package=mediapackage.CfnOriginEndpoint.HlsPackageProperty(
                segment_duration_seconds=6,
                playlist_window_seconds=60,
                playlist_type="NONE",
                ad_markers="NONE",
            ),
        )
        hls_ep.add_dependency(mp_channel)

        # ② LL-HLS endpoint — CMAF 1s 分片, PDT=1s (低延迟)
        ll_ep = mediapackage.CfnOriginEndpoint(
            self, "LlEndpoint",
            id=LL_ENDPOINT_ID,
            channel_id=mp_channel.id,
            manifest_name="index",
            startover_window_seconds=0,
            cmaf_package=mediapackage.CfnOriginEndpoint.CmafPackageProperty(
                segment_duration_seconds=1,
                hls_manifests=[mediapackage.CfnOriginEndpoint.HlsManifestProperty(
                    id="ll",
                    manifest_name="index",
                    playlist_window_seconds=30,
                    program_date_time_interval_seconds=1,
                    playlist_type="NONE",
                    ad_markers="NONE",
                )],
            ),
        )
        ll_ep.add_dependency(mp_channel)

        # ─────────────────────────────────────────────────────────────
        # 3) MediaLive Channel — 5 档 ABR, GOP=1s (encoder_settings 实测还原)
        # ─────────────────────────────────────────────────────────────
        ml_channel = medialive.CfnChannel(
            self, "Channel",
            name=CHANNEL_ID,
            channel_class="STANDARD",
            role_arn=ml_role.role_arn,
            input_specification=medialive.CfnChannel.InputSpecificationProperty(
                codec="AVC", maximum_bitrate="MAX_10_MBPS", resolution="HD"),
            input_attachments=[medialive.CfnChannel.InputAttachmentProperty(
                input_attachment_name=f"{PROJECT}-input", input_id=ml_input.ref)],
            destinations=[medialive.CfnChannel.OutputDestinationProperty(
                id="destination1",
                media_package_settings=[
                    medialive.CfnChannel.MediaPackageOutputDestinationSettingsProperty(channel_id=mp_channel.id)
                ],
            )],
        )
        # 大块 EncoderSettings 用 override 原样写入, 保证 PascalCase 不被改写
        ml_channel.add_property_override("EncoderSettings", ENCODER_SETTINGS)
        ml_channel.add_dependency(mp_channel)
        ml_channel.node.add_dependency(ml_role)

        # ─────────────────────────────────────────────────────────────
        # 4) Amazon IVS — 独立低延迟频道 (③)
        # ─────────────────────────────────────────────────────────────
        ivs_channel = ivs.CfnChannel(self, "IvsChannel", name=f"{PROJECT}-ivs",
                                     type="STANDARD", latency_mode="LOW")
        ivs_key = ivs.CfnStreamKey(self, "IvsKey", channel_arn=ivs_channel.attr_arn)

        # ─────────────────────────────────────────────────────────────
        # 5) CloudFront — 分发加速 + CORS, 同一 MediaPackage host 前置
        #    *.m3u8 不缓存 (manifest), 其余分片缓存 (best practice)
        # ─────────────────────────────────────────────────────────────
        mp_host = Fn.select(2, Fn.split("/", hls_ep.attr_url))  # <hash>.mediapackage.<region>.amazonaws.com
        cors = cf.ResponseHeadersPolicy(
            self, "CorsPolicy",
            cors_behavior=cf.ResponseHeadersCorsBehavior(
                access_control_allow_origins=["*"],
                access_control_allow_headers=["*"],
                access_control_allow_methods=["GET", "HEAD", "OPTIONS"],
                access_control_allow_credentials=False,
                origin_override=True,
            ),
        )
        mp_origin = origins.HttpOrigin(mp_host, protocol_policy=cf.OriginProtocolPolicy.HTTPS_ONLY)
        media_dist = cf.Distribution(
            self, "MediaDist",
            comment=f"{PROJECT} media (HLS + LL-HLS)",
            default_behavior=cf.BehaviorOptions(
                origin=mp_origin,
                viewer_protocol_policy=cf.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cf.CachePolicy.CACHING_OPTIMIZED,         # 分片可缓存
                response_headers_policy=cors,
            ),
            additional_behaviors={
                "*.m3u8": cf.BehaviorOptions(                          # manifest 不缓存
                    origin=mp_origin,
                    viewer_protocol_policy=cf.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    cache_policy=cf.CachePolicy.CACHING_DISABLED,
                    response_headers_policy=cors,
                ),
            },
        )

        # ─────────────────────────────────────────────────────────────
        # 6) 前端页面托管 — S3 (私有 + OAC) + CloudFront
        # ─────────────────────────────────────────────────────────────
        web_bucket = s3.Bucket(
            self, "WebBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )
        web_dist = cf.Distribution(
            self, "WebDist",
            comment=f"{PROJECT} player site",
            default_root_object="index.html",
            default_behavior=cf.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(web_bucket),
                viewer_protocol_policy=cf.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cf.CachePolicy.CACHING_DISABLED,  # demo: 始终拿最新页面/config
            ),
        )
        deploy = s3deploy.BucketDeployment(
            self, "DeployWeb",
            sources=[s3deploy.Source.asset(str(Path(__file__).parent.parent / "web"))],
            destination_bucket=web_bucket,
            distribution=web_dist,
            prune=False,  # 不要删掉 config.json (由下面的 CR 写入)
        )

        # ─────────────────────────────────────────────────────────────
        # 7) 自定义资源 — 部署后把真实地址写入 config.json
        #    (MediaPackage 端点 URL 含自动生成 GUID, 必须运行时读取)
        # ─────────────────────────────────────────────────────────────
        writer_fn = lambda_.Function(
            self, "ConfigWriterFn",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="index.on_event",
            timeout=Duration.minutes(2),
            code=lambda_.Code.from_asset(str(Path(__file__).parent.parent / "lambda" / "config_writer")),
        )
        web_bucket.grant_put(writer_fn)
        writer_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["mediapackage:DescribeOriginEndpoint"], resources=["*"]))

        provider = cr.Provider(self, "ConfigWriterProvider", on_event_handler=writer_fn)
        config_cr = CustomResource(
            self, "ConfigWriter",
            service_token=provider.service_token,
            properties={
                "BucketName": web_bucket.bucket_name,
                "MediaCfDomain": media_dist.distribution_domain_name,
                "HlsEndpointId": hls_ep.ref,
                "LlEndpointId": ll_ep.ref,
                "IvsPlaybackUrl": ivs_channel.attr_playback_url,
                "IvsIngestEndpoint": ivs_channel.attr_ingest_endpoint,
                "IvsStreamKey": ivs_key.attr_value,
                "MlIngestPrimary": Fn.select(0, ml_input.attr_destinations),
                "MlIngestSecondary": Fn.select(1, ml_input.attr_destinations),
            },
        )
        config_cr.node.add_dependency(deploy)
        config_cr.node.add_dependency(media_dist)

        # ─────────────────────────────────────────────────────────────
        # 8) 输出 — 按"部署完该干啥"排序; 值即为可直接复制执行的命令/地址
        # ─────────────────────────────────────────────────────────────
        ch = ml_channel.ref
        region = self.region
        site = f"https://{web_dist.distribution_domain_name}/"

        CfnOutput(self, "Step1OpenTestPage", value=site,
                  description="① 打开这个地址 = 三路对比测试页(推流命令已自动填好)")
        CfnOutput(self, "Step2StartChannel",
                  value=f"aws medialive start-channel --channel-id {ch} --region {region}",
                  description="② 启动 MediaLive(①标准HLS/②LL-HLS 需要; 启动后开始计费)")
        CfnOutput(self, "Step3PushStream", value=site,
                  description="③ 在测试页点'复制'拿 ffmpeg 推流命令运行; IVS 那路无需 start 直接推")
        CfnOutput(self, "Step4StopChannel",
                  value=f"aws medialive stop-channel --channel-id {ch} --region {region}",
                  description="④ 演示完停止 MediaLive(停止计费)")
        CfnOutput(self, "Step5Cleanup", value="cdk destroy",
                  description="⑤ 不再使用 = 删除全部资源")
