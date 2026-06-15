"""
MediaLive EncoderSettings — 还原自 "Live Streaming on AWS" (SO0013) 参考部署中
实际运行的 MediaLive 频道编码配置 (经 describe-channel 提取并验证)。

键名保持 CloudFormation PascalCase, 通过
add_property_override("EncoderSettings", ENCODER_SETTINGS) 原样写入模板,
避免 L1 struct 的键名转换带来的不确定性。

ABR 阶梯 (5 档, 全部 H264 MAIN + QVBR + GOP=1s, 为低延迟优化):
  512x288  @ 400 kbps  15fps  QVBR Q6
  640x360  @ 800 kbps  30fps  QVBR Q7
  768x432  @ 1.2 Mbps  30fps  QVBR Q7
  960x540  @ 1.8 Mbps  30fps  QVBR Q7
  1280x720 @ 2.7 Mbps  30fps  QVBR Q8
"""


def _aac(name: str) -> dict:
    return {
        "AudioSelectorName": "default",
        "AudioTypeControl": "FOLLOW_INPUT",
        "CodecSettings": {
            "AacSettings": {
                "Bitrate": 96000,
                "CodingMode": "CODING_MODE_2_0",
                "InputType": "NORMAL",
                "Profile": "LC",
                "RateControlMode": "CBR",
                "RawFormat": "NONE",
                "SampleRate": 48000,
                "Spec": "MPEG4",
            }
        },
        "LanguageCodeControl": "FOLLOW_INPUT",
        "Name": name,
    }


def _h264(width: int, height: int, bitrate: int, framerate: int, qvbr: int) -> dict:
    return {
        "CodecSettings": {
            "H264Settings": {
                "AdaptiveQuantization": "HIGH",
                "AfdSignaling": "NONE",
                "Bitrate": bitrate,
                "BufFillPct": 90,
                "BufSize": bitrate * 2,
                "ColorMetadata": "INSERT",
                "EntropyEncoding": "CAVLC",
                "FlickerAq": "ENABLED",
                "FramerateControl": "SPECIFIED",
                "FramerateDenominator": 1,
                "FramerateNumerator": framerate,
                "GopBReference": "ENABLED",
                "GopClosedCadence": 1,
                "GopNumBFrames": 3,
                "GopSize": 1.0,
                "GopSizeUnits": "SECONDS",
                "Level": "H264_LEVEL_AUTO",
                "LookAheadRateControl": "HIGH",
                "MaxBitrate": bitrate,
                "NumRefFrames": 5,
                "ParControl": "SPECIFIED",
                "ParDenominator": 1,
                "ParNumerator": 1,
                "Profile": "MAIN",
                "QvbrQualityLevel": qvbr,
                "RateControlMode": "QVBR",
                "ScanType": "PROGRESSIVE",
                "SceneChangeDetect": "ENABLED",
                "SpatialAq": "ENABLED",
                "SubgopLength": "DYNAMIC",
                "Syntax": "DEFAULT",
                "TemporalAq": "ENABLED",
                "TimecodeInsertion": "DISABLED",
            }
        },
        "Height": height,
        "Name": f"_{width}x{height}",
        "RespondToAfd": "NONE",
        "ScalingBehavior": "DEFAULT",
        "Sharpness": 100,
        "Width": width,
    }


# 5 档视频 + 对应 5 路音频, 与线上 OutputGroup "HLS HD" 完全一致
_LADDER = [
    # (width, height, bitrate, framerate, qvbr, audio_name)
    (512, 288, 400000, 15, 6, "audio_j8tr8"),
    (640, 360, 800000, 30, 7, "audio_6ht2vm"),
    (768, 432, 1200000, 30, 7, "audio_s90hue"),
    (960, 540, 1800000, 30, 7, "audio_i3rm19"),
    (1280, 720, 2700000, 30, 8, "audio_ze3rtr"),
]

ENCODER_SETTINGS = {
    "AudioDescriptions": [_aac(a) for (_, _, _, _, _, a) in _LADDER],
    "AvailConfiguration": {
        "AvailSettings": {
            "Scte35SpliceInsert": {
                "NoRegionalBlackoutFlag": "FOLLOW",
                "WebDeliveryAllowedFlag": "FOLLOW",
            }
        }
    },
    "CaptionDescriptions": [],
    "OutputGroups": [
        {
            "Name": "HLS HD",
            "OutputGroupSettings": {
                "MediaPackageGroupSettings": {
                    "Destination": {"DestinationRefId": "destination1"}
                }
            },
            "Outputs": [
                {
                    "AudioDescriptionNames": [a],
                    "CaptionDescriptionNames": [],
                    "OutputName": f"_{w}x{h}",
                    "OutputSettings": {"MediaPackageOutputSettings": {}},
                    "VideoDescriptionName": f"_{w}x{h}",
                }
                for (w, h, _, _, _, a) in _LADDER
            ],
        }
    ],
    "TimecodeConfig": {"Source": "EMBEDDED"},
    "VideoDescriptions": [
        _h264(w, h, b, fr, q) for (w, h, b, fr, q, _) in _LADDER
    ],
}
