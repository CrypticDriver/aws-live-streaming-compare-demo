"""
自定义资源 Lambda: 部署后把真实的播放/推流地址写入网站桶的 config.json。

为什么需要它:
  MediaPackage origin endpoint 的播放 URL 里含自动生成的 GUID
  (.../out/v1/<guid>/index.m3u8), 在 synth 阶段拿不到, 必须部署后
  调 DescribeOriginEndpoint 读取真实 URL, 再把 host 换成 CloudFront 域名。

由 aws_cdk.custom_resources.Provider 包装, on_event 返回即可, 无需手写 cfnresponse。
"""
import json
import os
from urllib.parse import urlparse

import boto3

mp = boto3.client("mediapackage")
s3 = boto3.client("s3")


def _cf_url(media_package_url: str, cf_domain: str) -> str:
    """把 MediaPackage 播放 URL 的 host 换成 CloudFront 域名, 路径保持不变。"""
    p = urlparse(media_package_url)
    return f"https://{cf_domain}{p.path}"


def on_event(event, context):
    request_type = event.get("RequestType")
    props = event.get("ResourceProperties", {})
    print("RequestType=%s props=%s" % (request_type, json.dumps({k: v for k, v in props.items() if k != "ServiceToken"})))

    if request_type == "Delete":
        # 网站桶随 stack 删除 (auto_delete_objects), 这里无需处理
        return {"PhysicalResourceId": event.get("PhysicalResourceId", "config-writer")}

    bucket = props["BucketName"]
    cf_domain = props["MediaCfDomain"]

    # 标准 HLS endpoint
    hls = mp.describe_origin_endpoint(Id=props["HlsEndpointId"])
    hls_url = hls["Url"]

    # LL-HLS (CMAF) endpoint — URL 在 CmafPackage.HlsManifests[].Url
    ll = mp.describe_origin_endpoint(Id=props["LlEndpointId"])
    ll_url = ll["CmafPackage"]["HlsManifests"][0]["Url"]

    config = {
        "std": _cf_url(hls_url, cf_domain),   # ① 标准 HLS (经 CloudFront)
        "low": _cf_url(ll_url, cf_domain),    # ② LL-HLS   (经 CloudFront)
        "ivs": props["IvsPlaybackUrl"],       # ③ IVS 播放地址
        "ingest": {
            "mediaLivePrimary": props.get("MlIngestPrimary", ""),
            "mediaLiveSecondary": props.get("MlIngestSecondary", ""),
            "ivsEndpoint": props.get("IvsIngestEndpoint", ""),
            "ivsStreamKey": props.get("IvsStreamKey", ""),
        },
        "region": os.environ.get("AWS_REGION", ""),
    }

    s3.put_object(
        Bucket=bucket,
        Key="config.json",
        Body=json.dumps(config, ensure_ascii=False, indent=2).encode("utf-8"),
        ContentType="application/json",
        CacheControl="no-cache, max-age=0",
    )
    print("wrote config.json: %s" % json.dumps(config, ensure_ascii=False))
    return {"PhysicalResourceId": f"config-{bucket}", "Data": config}
