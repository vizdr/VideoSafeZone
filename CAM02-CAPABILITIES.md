# cam-02 — ONVIF capabilities, as the camera reports them

Queried live on 2026-09-26 19:09 UTC from the adapter (read-only calls only — nothing on the camera was
changed). This is the camera's **own answer** to every capability query its services
accept; for what was actually *verified* to work, and for everything ONVIF cannot see
(web-UI-only settings, silent no-op writes), `Camera-Features.md` is authoritative.
Advertised is not verified: this camera has accepted writes and ignored them
(`Camera-Features.md` §4).

Stream URIs are redacted: the camera appends `?username=…&password=<MD5 of the password>`.
The camera serves no WSDL of its own (every `?wsdl` URL returns its 817-byte HTML stub); the
standard ONVIF WSDLs for these services are listed in §2.

---

## 1. Device

| Field | Value |
|---|---|
| Manufacturer | `A_ONVIF_CAMERA` |
| Model | `YMA42P_C2_WM701_AF` |
| FirmwareVersion | `V3.3.2.1 build 2024-12-26 16:26:58` |
| SerialNumber | `EF00000006108447` |
| HardwareId | `1419d68a-1dd2-11b2-a105-F00006108447` |
| ONVIF profiles (scopes) | Streaming (= Profile S) |

## 2. Services

| Service namespace | Version | Endpoint | Standard WSDL |
|---|---|---|---|
| `http://www.onvif.org/ver10/device/wsdl` | 1.11 | `http://192.168.178.67:80/Device` | https://www.onvif.org/ver10/device/wsdl/devicemgmt.wsdl |
| `http://www.onvif.org/ver10/media/wsdl` | 1.11 | `http://192.168.178.67:80/Media` | https://www.onvif.org/ver10/media/wsdl/media.wsdl |
| `http://www.onvif.org/ver20/media/wsdl` | 1.11 | `http://192.168.178.67:80/Media2` | https://www.onvif.org/ver20/media/wsdl/media.wsdl |
| `http://www.onvif.org/ver20/imaging/wsdl` | 1.11 | `http://192.168.178.67:80/Imaging` | https://www.onvif.org/ver20/imaging/wsdl/imaging.wsdl |
| `http://www.onvif.org/ver10/events/wsdl` | 1.11 | `http://192.168.178.67:80/Event` | https://www.onvif.org/ver10/events/wsdl/event.wsdl |
| `http://www.onvif.org/ver20/analytics/wsdl` | 1.11 | `http://192.168.178.67:80/Analytics` | https://www.onvif.org/ver20/analytics/wsdl/analytics.wsdl |
| `http://www.onvif.org/ver20/ptz/wsdl` | 1.11 | `http://192.168.178.67:80/PTZ` | https://www.onvif.org/ver20/ptz/wsdl/ptz.wsdl |
| `http://www.onvif.org/ver10/search/wsdl` | 1.11 | `http://192.168.178.67:80/Search` | https://www.onvif.org/ver10/search.wsdl |
| `http://www.onvif.org/ver10/replay/wsdl` | 1.11 | `http://192.168.178.67:80/Replay` | https://www.onvif.org/ver10/replay.wsdl |
| `http://www.onvif.org/ver10/recording/wsdl` | 1.11 | `http://192.168.178.67:80/Recording` | https://www.onvif.org/ver10/recording.wsdl |
| `http://www.onvif.org/ver10/deviceIO/wsdl` | 1.11 | `http://192.168.178.67:80/DeviceIO` | https://www.onvif.org/ver10/deviceio.wsdl |
| `http://www.onvif.org/ver10/plus/wsdl` | 1.11 | `http://192.168.178.67:80/Plus` | — (vendor, not public) |

Every service reports version 1.11 regardless of its actual ONVIF release — the camera
fills the field with one value, so read it as a firmware constant, not per-service.

## 3. Media profiles and video encoders (Media2)

| Profile | Video source | Encoder | Codec now | Resolution | fps | Bitrate | GOP | Audio encoder |
|---|---|---|---|---|---|---|---|---|
| `MainStream` | `VideoSourceMain` | `VideoEncodeMain` | H264 (High) | 2560×1440 | 15 | 3000 kbps (VBR) | 45 | G711A, Bitrate=64000, SampleRate=8000 |
| `SubStream` | `VideoSourceMain` | `VideoEncodeSub` | H265 (High) | 640×360 | 15 | 500 kbps (VBR) | 45 | G711A, Bitrate=64000, SampleRate=8000 |

Quirks in this table, both as the camera reports them: the `Profile` attribute keeps saying
`High` after a switch to H.265 (the bitstream is Main), and the audio encoder's `Bitrate`
reads 64000 while its options list 64 — kbps per the Media2 schema, so the config value is
off by 1000. The options below also say `ProfilesSupported=Main` for H.264, while the
configurations run High.

**What each encoder can be set to** (`GetVideoEncoderConfigurationOptions`):

| Encoder | Codec | Resolutions | Bitrate range | fps | GOP range | Profiles | Quality |
|---|---|---|---|---|---|---|---|
| `VideoEncodeMain` | H264 | 2560×1440, 2304×1296, 1920×1080, 1280×720 | 512–9216 kbps | 1-30 | 1–200 | Main | 10–100 |
| `VideoEncodeMain` | H265 | 2560×1440, 2304×1296, 1920×1080, 1280×720 | 128–8192 kbps | 1-30 | 1–200 | Main | 10–100 |
| `VideoEncodeSub` | H264 | 640×360, 480×360, 352×288 | 64–2048 kbps | 1-30 | 1–200 | Main | 10–100 |
| `VideoEncodeSub` | H265 | 640×360, 480×360, 352×288 | 64–2048 kbps | 1-30 | 1–200 | Main | 10–100 |

Frame-rate and GOP ranges are the extremes of the lists the camera returns. H.265 is visible
only here, through Media2 — Media1's encoding enum stops at H.264 (guide §21.1).

## 4. Audio encoders

| Encoder | Codec | Bitrates | Sample rates |
|---|---|---|---|
| `G711A` | G711A | 64 kbps | 8, 16 kHz |

G.711 only; the camera does not offer AAC. The producer transcodes to AAC because KVS
playback requires it (guide §18.1).

## 5. Imaging options

| Option | Range / values |
|---|---|
| IR-cut filter modes | ON, OFF, AUTO |
| Brightness | 1–255 |
| Colour saturation | 1–255 |
| Contrast | 1–255 |
| Sharpness | 1–255 |
| Exposure modes | AUTO, MANUAL |
| White-balance modes | AUTO, MANUAL |
| Focus modes | MANUAL |
| Backlight comp. | ON |
| Wide dynamic range | OFF, ON |

## 6. Event topics

Topics the event service advertises (`GetEventProperties`):

- `tns1:RuleEngine/CellMotionDetector/Motion`
- `tns1:VideoSource/MotionAlarm`
- `tns1:Device/Trigger/AlarmIn`
- `tns1:Device/Trigger/DigitalInput`
- `tns1:Device/Trigger/Relay`
- `tns1:UserAlarm/IVA/HumanShapeDetect`

Topic expression dialects: `http://www.onvif.org/ver10/tev/topicExpression/ConcreteSet`, `http://docs.oasis-open.org/wsn/t-1/TopicExpression/Concrete`

## 7. Analytics, PTZ and DeviceIO

| | |
|---|---|
| Supported rule types | `tt:CellMotionDetector`, `tt:SmartMotionDetector`, `tt:LineDetector` |
| Configured rules | `MyMotionDetectorRule` (tt:CellMotionDetector), `MySmartMotionDetector` (tt:SmartMotionDetector) |
| Supported analytics modules | `tt:CellMotionEngine`, `tt:CellMotionDetector`, `tt:SmartMotionDetectorEngine`, `tt:LineDetectorEngine` |
| Configured modules | `MyCellMotionModule` (tt:CellMotionEngine), `MyMotionDetectorRule` (tt:CellMotionDetector), `MySmartMotionDetector` (tt:SmartMotionDetectorEngine) |
| PTZ nodes | 1 |
| DeviceIO video sources | 1 |
| DeviceIO audio sources | 1 |
| DeviceIO audio outputs | 0 |
| Digital (alarm) inputs | 1 |

The ONVIF 2.0 analytics service **answers these read calls** (raw SOAP; only its
`GetServiceCapabilities` is refused). Writing analytics settings over ONVIF is the part that
is a silent no-op on this camera (`Camera-Features.md` §4).

## 8. Calls the camera refused

Advertised services that fault on the call are a finding in themselves (`Camera-Features.md`
"What does not work").

| Service | Call | Reply |
|---|---|---|
| Imaging | `GetServiceCapabilities` | SOAP fault (HTTP 400): SOAP-ENV:Sender |
| Analytics | `GetServiceCapabilities` | SOAP fault (HTTP 400): SOAP-ENV:Sender |
| PTZ | `GetServiceCapabilities` | SOAP fault (HTTP 400): SOAP-ENV:Sender |
| DeviceIO | `GetRelayOutputs` | SOAP fault (HTTP 400): SOAP-ENV:Sender / wsa:ActionNotSupported / The [action] cannot be processed at the receiver. |
| Recording | `GetServiceCapabilities` | SOAP fault (HTTP 400): SOAP-ENV:Sender / wsa:ActionNotSupported / The [action] cannot be processed at the receiver. |
| Recording | `GetRecordings` | SOAP fault (HTTP 400): SOAP-ENV:Sender / wsa:ActionNotSupported / The [action] cannot be processed at the receiver. |
| Search | `GetServiceCapabilities` | SOAP fault (HTTP 400): SOAP-ENV:Sender / wsa:ActionNotSupported / The [action] cannot be processed at the receiver. |
| Search | `GetRecordingSummary` | SOAP fault (HTTP 400): SOAP-ENV:Sender / wsa:ActionNotSupported / The [action] cannot be processed at the receiver. |
| Replay | `GetServiceCapabilities` | SOAP fault (HTTP 400): SOAP-ENV:Sender / wsa:ActionNotSupported / The [action] cannot be processed at the receiver. |

---

## 9. Raw replies

Each reply exactly as returned, pretty-printed, credentials redacted.

### Device

<details><summary><code>GetSystemDateAndTime</code></summary>

```xml
<tds:GetSystemDateAndTimeResponse xmlns:tds="http://www.onvif.org/ver10/device/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
  <tds:SystemDateAndTime>
    <tt:DateTimeType>NTP</tt:DateTimeType>
    <tt:DaylightSavings>false</tt:DaylightSavings>
    <tt:TimeZone>
      <tt:TZ>CET-1CEST-2,M3.5.0/02:00:00,M10.5.0/03:00:00</tt:TZ>
    </tt:TimeZone>
    <tt:UTCDateTime>
      <tt:Time>
        <tt:Hour>20</tt:Hour>
        <tt:Minute>9</tt:Minute>
        <tt:Second>12</tt:Second>
      </tt:Time>
      <tt:Date>
        <tt:Year>2026</tt:Year>
        <tt:Month>9</tt:Month>
        <tt:Day>26</tt:Day>
      </tt:Date>
    </tt:UTCDateTime>
    <tt:LocalDateTime>
      <tt:Time>
        <tt:Hour>21</tt:Hour>
        <tt:Minute>9</tt:Minute>
        <tt:Second>12</tt:Second>
      </tt:Time>
      <tt:Date>
        <tt:Year>2026</tt:Year>
        <tt:Month>9</tt:Month>
        <tt:Day>26</tt:Day>
      </tt:Date>
    </tt:LocalDateTime>
  </tds:SystemDateAndTime>
</tds:GetSystemDateAndTimeResponse>
```

</details>

<details><summary><code>GetDeviceInformation</code></summary>

```xml
<tds:GetDeviceInformationResponse xmlns:tds="http://www.onvif.org/ver10/device/wsdl">
  <tds:Manufacturer>A_ONVIF_CAMERA</tds:Manufacturer>
  <tds:Model>YMA42P_C2_WM701_AF</tds:Model>
  <tds:FirmwareVersion>V3.3.2.1 build 2024-12-26 16:26:58 
</tds:FirmwareVersion>
  <tds:SerialNumber>EF00000006108447</tds:SerialNumber>
  <tds:HardwareId>1419d68a-1dd2-11b2-a105-F00006108447</tds:HardwareId>
</tds:GetDeviceInformationResponse>
```

</details>

<details><summary><code>GetScopes</code></summary>

```xml
<tds:GetScopesResponse xmlns:tds="http://www.onvif.org/ver10/device/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
  <tds:Scopes>
    <tt:ScopeDef>Configurable</tt:ScopeDef>
    <tt:ScopeItem>onvif://www.onvif.org/type/Network_Video_Transmitter</tt:ScopeItem>
  </tds:Scopes>
  <tds:Scopes>
    <tt:ScopeDef>Configurable</tt:ScopeDef>
    <tt:ScopeItem>onvif://www.onvif.org/type/ptz</tt:ScopeItem>
  </tds:Scopes>
  <tds:Scopes>
    <tt:ScopeDef>Configurable</tt:ScopeDef>
    <tt:ScopeItem>onvif://www.onvif.org/type/video_encoder</tt:ScopeItem>
  </tds:Scopes>
  <tds:Scopes>
    <tt:ScopeDef>Configurable</tt:ScopeDef>
    <tt:ScopeItem>onvif://www.onvif.org/type/audio_encoder</tt:ScopeItem>
  </tds:Scopes>
  <tds:Scopes>
    <tt:ScopeDef>Configurable</tt:ScopeDef>
    <tt:ScopeItem>onvif://www.onvif.org/type/video_analytics</tt:ScopeItem>
  </tds:Scopes>
  <tds:Scopes>
    <tt:ScopeDef>Fixed</tt:ScopeDef>
    <tt:ScopeItem>onvif://www.onvif.org/hardware/YMA42P_C2_WM701</tt:ScopeItem>
  </tds:Scopes>
  <tds:Scopes>
    <tt:ScopeDef>Fixed</tt:ScopeDef>
    <tt:ScopeItem>onvif://www.onvif.org/name/A_ONVIF_CAMERA</tt:ScopeItem>
  </tds:Scopes>
  <tds:Scopes>
    <tt:ScopeDef>Fixed</tt:ScopeDef>
    <tt:ScopeItem>onvif://www.onvif.org/Profile/Streaming</tt:ScopeItem>
  </tds:Scopes>
  <tds:Scopes>
    <tt:ScopeDef>Configurable</tt:ScopeDef>
    <tt:ScopeItem>onvif://www.onvif.org/location/China</tt:ScopeItem>
  </tds:Scopes>
  <tds:Scopes>
    <tt:ScopeDef>Configurable</tt:ScopeDef>
    <tt:ScopeItem>onvif://www.onvif.org/location/Shenzhen</tt:ScopeItem>
  </tds:Scopes>
</tds:GetScopesResponse>
```

</details>

<details><summary><code>GetServices (IncludeCapability=true)</code></summary>

```xml
<tds:GetServicesResponse xmlns:tds="http://www.onvif.org/ver10/device/wsdl" xmlns:tev="http://www.onvif.org/ver10/events/wsdl" xmlns:timg="http://www.onvif.org/ver20/imaging/wsdl" xmlns:tmd="http://www.onvif.org/ver10/deviceIO/wsdl" xmlns:tr2="http://www.onvif.org/ver20/media/wsdl" xmlns:trt="http://www.onvif.org/ver10/media/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
  <tds:Service>
    <tds:Namespace>http://www.onvif.org/ver10/device/wsdl</tds:Namespace>
    <tds:XAddr>http://192.168.178.67:80/Device</tds:XAddr>
    <tds:Capabilities> </tds:Capabilities>
    <tds:Version>
      <tt:Major>1</tt:Major>
      <tt:Minor>11</tt:Minor>
    </tds:Version>
  </tds:Service>
  <tds:Service>
    <tds:Namespace>http://www.onvif.org/ver10/media/wsdl</tds:Namespace>
    <tds:XAddr>http://192.168.178.67:80/Media</tds:XAddr>
    <tds:Capabilities>
      <trt:Capabilities SnapshotUri="true" Rotation="false" VideoSourceMode="false" OSD="true">
        <trt:ProfileCapabilities MaximumNumberOfProfiles="10" ConfigurationsSupported="VideoSource VideoEncoder AudioSource AudioEncoder" />
        <trt:StreamingCapabilities RTPMulticast="true" RTP_TCP="true" RTP_RTSP_TCP="true" NonAggregateControl="false" />
      </trt:Capabilities>
    </tds:Capabilities>
    <tds:Version>
      <tt:Major>1</tt:Major>
      <tt:Minor>11</tt:Minor>
    </tds:Version>
  </tds:Service>
  <tds:Service>
    <tds:Namespace>http://www.onvif.org/ver20/media/wsdl</tds:Namespace>
    <tds:XAddr>http://192.168.178.67:80/Media2</tds:XAddr>
    <tds:Capabilities>
      <tr2:Capabilities SnapshotUri="true" Rotation="false" VideoSourceMode="false" OSD="true" Mask="true">
        <tr2:ProfileCapabilities MaximumNumberOfProfiles="10" ConfigurationsSupported="VideoSource VideoEncoder AudioSource AudioEncoder" />
        <tr2:StreamingCapabilities RTSPStreaming="true" RTPMulticast="true" RTP_RTSP_TCP="true" NonAggregateControl="false" />
      </tr2:Capabilities>
    </tds:Capabilities>
    <tds:Version>
      <tt:Major>1</tt:Major>
      <tt:Minor>11</tt:Minor>
    </tds:Version>
  </tds:Service>
  <tds:Service>
    <tds:Namespace>http://www.onvif.org/ver20/imaging/wsdl</tds:Namespace>
    <tds:XAddr>http://192.168.178.67:80/Imaging</tds:XAddr>
    <tds:Capabilities>
      <timg:Capabilities ImageStabilization="false" />
    </tds:Capabilities>
    <tds:Version>
      <tt:Major>1</tt:Major>
      <tt:Minor>11</tt:Minor>
    </tds:Version>
  </tds:Service>
  <tds:Service>
    <tds:Namespace>http://www.onvif.org/ver10/events/wsdl</tds:Namespace>
    <tds:XAddr>http://192.168.178.67:80/Event</tds:XAddr>
    <tds:Capabilities>
      <tev:Capabilities WSSubscriptionPolicySupport="true" WSPullPointSupport="true" WSPausableSubscriptionManagerInterfaceSupport="true" MaxNotificationProducers="10" MaxPullPoints="10" />
    </tds:Capabilities>
    <tds:Version>
      <tt:Major>1</tt:Major>
      <tt:Minor>11</tt:Minor>
    </tds:Version>
  </tds:Service>
  <tds:Service>
    <tds:Namespace>http://www.onvif.org/ver20/analytics/wsdl</tds:Namespace>
    <tds:XAddr>http://192.168.178.67:80/Analytics</tds:XAddr>
    <tds:Capabilities> </tds:Capabilities>
    <tds:Version>
      <tt:Major>1</tt:Major>
      <tt:Minor>11</tt:Minor>
    </tds:Version>
  </tds:Service>
  <tds:Service>
    <tds:Namespace>http://www.onvif.org/ver20/ptz/wsdl</tds:Namespace>
    <tds:XAddr>http://192.168.178.67:80/PTZ</tds:XAddr>
    <tds:Capabilities> </tds:Capabilities>
    <tds:Version>
      <tt:Major>1</tt:Major>
      <tt:Minor>11</tt:Minor>
    </tds:Version>
  </tds:Service>
  <tds:Service>
    <tds:Namespace>http://www.onvif.org/ver10/search/wsdl</tds:Namespace>
    <tds:XAddr>http://192.168.178.67:80/Search</tds:XAddr>
    <tds:Capabilities> </tds:Capabilities>
    <tds:Version>
      <tt:Major>1</tt:Major>
      <tt:Minor>11</tt:Minor>
    </tds:Version>
  </tds:Service>
  <tds:Service>
    <tds:Namespace>http://www.onvif.org/ver10/replay/wsdl</tds:Namespace>
    <tds:XAddr>http://192.168.178.67:80/Replay</tds:XAddr>
    <tds:Capabilities> </tds:Capabilities>
    <tds:Version>
      <tt:Major>1</tt:Major>
      <tt:Minor>11</tt:Minor>
    </tds:Version>
  </tds:Service>
  <tds:Service>
    <tds:Namespace>http://www.onvif.org/ver10/recording/wsdl</tds:Namespace>
    <tds:XAddr>http://192.168.178.67:80/Recording</tds:XAddr>
    <tds:Capabilities> </tds:Capabilities>
    <tds:Version>
      <tt:Major>1</tt:Major>
      <tt:Minor>11</tt:Minor>
    </tds:Version>
  </tds:Service>
  <tds:Service>
    <tds:Namespace>http://www.onvif.org/ver10/deviceIO/wsdl</tds:Namespace>
    <tds:XAddr>http://192.168.178.67:80/DeviceIO</tds:XAddr>
    <tds:Capabilities>
      <tmd:Capabilities VideoSources="1" VideoOutputs="0" AudioSources="1" AudioOutputs="1" RelayOutputs="1" DigitalInputs="1" SerialPorts="0" DigitalInputOptions="true" />
    </tds:Capabilities>
    <tds:Version>
      <tt:Major>1</tt:Major>
      <tt:Minor>11</tt:Minor>
    </tds:Version>
  </tds:Service>
  <tds:Service>
    <tds:Namespace>http://www.onvif.org/ver10/plus/wsdl</tds:Namespace>
    <tds:XAddr>http://192.168.178.67:80/Plus</tds:XAddr>
    <tds:Capabilities>
      <tmd:Capabilities VideoSources="1" VideoOutputs="0" AudioSources="1" AudioOutputs="1" RelayOutputs="1" DigitalInputs="1" SerialPorts="0" DigitalInputOptions="true" />
    </tds:Capabilities>
    <tds:Version>
      <tt:Major>1</tt:Major>
      <tt:Minor>11</tt:Minor>
    </tds:Version>
  </tds:Service>
</tds:GetServicesResponse>
```

</details>

<details><summary><code>GetCapabilities (All)</code></summary>

```xml
<tds:GetCapabilitiesResponse xmlns:ns2="http://www.onvifext.com/onvif/ext/ver10/wsdl" xmlns:ns3="http://www.onvifext.com/onvif/ext/ver10/schema" xmlns:ns4="http://www.onvif.org/ver10/plus/schema" xmlns:tds="http://www.onvif.org/ver10/device/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
  <tds:Capabilities>
    <tt:Analytics>
      <tt:XAddr>http://192.168.178.67:80/onvif/analytics</tt:XAddr>
      <tt:RuleSupport>true</tt:RuleSupport>
      <tt:AnalyticsModuleSupport>true</tt:AnalyticsModuleSupport>
    </tt:Analytics>
    <tt:Device>
      <tt:XAddr>http://192.168.178.67:80/onvif/device</tt:XAddr>
      <tt:Network>
        <tt:IPFilter>false</tt:IPFilter>
        <tt:ZeroConfiguration>false</tt:ZeroConfiguration>
        <tt:IPVersion6>false</tt:IPVersion6>
        <tt:DynDNS>false</tt:DynDNS>
        <tt:Extension>
          <tt:Dot11Configuration>false</tt:Dot11Configuration>
        </tt:Extension>
      </tt:Network>
      <tt:System>
        <tt:DiscoveryResolve>false</tt:DiscoveryResolve>
        <tt:DiscoveryBye>false</tt:DiscoveryBye>
        <tt:RemoteDiscovery>false</tt:RemoteDiscovery>
        <tt:SystemBackup>false</tt:SystemBackup>
        <tt:SystemLogging>true</tt:SystemLogging>
        <tt:FirmwareUpgrade>true</tt:FirmwareUpgrade>
        <tt:SupportedVersions>
          <tt:Major>17</tt:Major>
          <tt:Minor>6</tt:Minor>
        </tt:SupportedVersions>
        <tt:Extension>
          <tt:HttpFirmwareUpgrade>true</tt:HttpFirmwareUpgrade>
          <tt:HttpSystemBackup>false</tt:HttpSystemBackup>
          <tt:HttpSystemLogging>true</tt:HttpSystemLogging>
          <tt:HttpSupportInformation>true</tt:HttpSupportInformation>
        </tt:Extension>
      </tt:System>
      <tt:IO>
        <tt:InputConnectors>1</tt:InputConnectors>
        <tt:RelayOutputs>1</tt:RelayOutputs>
      </tt:IO>
      <tt:Security>
        <tt:TLS1.1>false</tt:TLS1.1>
        <tt:TLS1.2>false</tt:TLS1.2>
        <tt:OnboardKeyGeneration>false</tt:OnboardKeyGeneration>
        <tt:AccessPolicyConfig>false</tt:AccessPolicyConfig>
        <tt:X.509Token>false</tt:X.509Token>
        <tt:SAMLToken>false</tt:SAMLToken>
        <tt:KerberosToken>false</tt:KerberosToken>
        <tt:RELToken>false</tt:RELToken>
      </tt:Security>
    </tt:Device>
    <tt:Events>
      <tt:XAddr>http://192.168.178.67:80/onvif/events</tt:XAddr>
      <tt:WSSubscriptionPolicySupport>true</tt:WSSubscriptionPolicySupport>
      <tt:WSPullPointSupport>true</tt:WSPullPointSupport>
      <tt:WSPausableSubscriptionManagerInterfaceSupport>true</tt:WSPausableSubscriptionManagerInterfaceSupport>
    </tt:Events>
    <tt:Imaging>
      <tt:XAddr>http://192.168.178.67:80/onvif/imaging</tt:XAddr>
    </tt:Imaging>
    <tt:Media>
      <tt:XAddr>http://192.168.178.67:80/onvif/media</tt:XAddr>
      <tt:StreamingCapabilities>
        <tt:RTPMulticast>true</tt:RTPMulticast>
        <tt:RTP_TCP>true</tt:RTP_TCP>
        <tt:RTP_RTSP_TCP>true</tt:RTP_RTSP_TCP>
      </tt:StreamingCapabilities>
    </tt:Media>
    <tt:PTZ>
      <tt:XAddr>http://192.168.178.67:80/onvif/ptz</tt:XAddr>
    </tt:PTZ>
    <tt:Extension>
      <tt:DeviceIO>
        <tt:XAddr>http://192.168.178.67:80/onvif/deviceIO</tt:XAddr>
        <tt:VideoSources>1</tt:VideoSources>
        <tt:VideoOutputs>0</tt:VideoOutputs>
        <tt:AudioSources>1</tt:AudioSources>
        <tt:AudioOutputs>1</tt:AudioOutputs>
        <tt:RelayOutputs>1</tt:RelayOutputs>
      </tt:DeviceIO>
      <tt:Extensions>
        <tt:TelexCapabilities>
          <tt:XAddr>http://192.168.178.67:80/onvif/telecom_service</tt:XAddr>
          <tt:TimeOSDSupport>true</tt:TimeOSDSupport>
          <tt:TitleOSDSupport>true</tt:TitleOSDSupport>
          <tt:PTZ3DZoomSupport>true</tt:PTZ3DZoomSupport>
          <tt:PTZAuxSwitchSupport>true</tt:PTZAuxSwitchSupport>
          <tt:MotionDetectorSupport>true</tt:MotionDetectorSupport>
          <tt:TamperDetectorSupport>true</tt:TamperDetectorSupport>
        </tt:TelexCapabilities>
      </tt:Extensions>
      <ns2:hbCapabilities>
        <ns3:XAddr>http://192.168.178.67:80/onvif/hbgk_ext</ns3:XAddr>
        <ns3:H265Support>true</ns3:H265Support>
        <ns3:PrivacyMaskSupport>true</ns3:PrivacyMaskSupport>
        <ns3:CameraNum>1</ns3:CameraNum>
        <ns3:MaxMaskAreaNum>4</ns3:MaxMaskAreaNum>
      </ns2:hbCapabilities>
      <ns4:Plus>
        <ns4:XAddr>http://192.168.178.67:80/onvif/hbgk_ext</ns4:XAddr>
        <ns4:H265>true</ns4:H265>
        <ns4:PrivacyMask>true</ns4:PrivacyMask>
        <ns4:CameraNum>1</ns4:CameraNum>
        <ns4:MaxMaskAreaNum>4</ns4:MaxMaskAreaNum>
      </ns4:Plus>
    </tt:Extension>
  </tds:Capabilities>
</tds:GetCapabilitiesResponse>
```

</details>

<details><summary><code>GetServiceCapabilities</code></summary>

```xml
<tds:GetServiceCapabilitiesResponse xmlns:tds="http://www.onvif.org/ver10/device/wsdl">
  <tds:Capabilities>
    <tds:Network IPFilter="false" ZeroConfiguration="false" IPVersion6="false" DynDNS="false" Dot11Configuration="false" HostnameFromDHCP="false" NTP="1" />
    <tds:Security TLS1.0="false" TLS1.1="false" TLS1.2="false" OnboardKeyGeneration="false" AccessPolicyConfig="false" Dot1X="false" RemoteUserHandling="false" X.509Token="false" SAMLToken="false" KerberosToken="false" UsernameToken="false" HttpDigest="false" RELToken="false" />
    <tds:System DiscoveryResolve="false" DiscoveryBye="false" RemoteDiscovery="false" SystemBackup="false" SystemLogging="true" FirmwareUpgrade="true" HttpFirmwareUpgrade="true" HttpSystemBackup="false" HttpSystemLogging="true" HttpSupportInformation="true" />
  </tds:Capabilities>
</tds:GetServiceCapabilitiesResponse>
```

</details>

### Media2

<details><summary><code>GetServiceCapabilities</code></summary>

```xml
<tr2:GetServiceCapabilitiesResponse2 xmlns:tr2="http://www.onvif.org/ver20/media/wsdl">
  <tr2:Capabilities SnapshotUri="true" Rotation="false" OSD="true">
    <tr2:ProfileCapabilities MaximumNumberOfProfiles="10" />
    <tr2:StreamingCapabilities RTSPStreaming="true" RTPMulticast="true" RTP_RTSP_TCP="true" NonAggregateControl="false" />
  </tr2:Capabilities>
</tr2:GetServiceCapabilitiesResponse2>
```

</details>

<details><summary><code>GetProfiles (Type=All)</code></summary>

```xml
<tr2:GetProfilesResponse xmlns:tr2="http://www.onvif.org/ver20/media/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
  <tr2:Profiles token="MainStream" fixed="true">
    <tr2:Name>MainStream</tr2:Name>
    <tr2:Configurations>
      <tr2:VideoSource token="VideoSourceMain">
        <tt:Name>VideoSourceMain</tt:Name>
        <tt:UseCount>2</tt:UseCount>
        <tt:SourceToken>VideoSourceMain</tt:SourceToken>
        <tt:Bounds x="0" y="0" width="2560" height="1440" />
      </tr2:VideoSource>
      <tr2:AudioSource token="AudioMainToken">
        <tt:Name>AudioMainName</tt:Name>
        <tt:UseCount>2</tt:UseCount>
        <tt:SourceToken>AudioMainSrcToken</tt:SourceToken>
      </tr2:AudioSource>
      <tr2:VideoEncoder token="VideoEncodeMain" GovLength="45" Profile="High">
        <tt:Name>VideoEncodeMain</tt:Name>
        <tt:UseCount>1</tt:UseCount>
        <tt:Encoding>H264</tt:Encoding>
        <tt:Resolution>
          <tt:Width>2560</tt:Width>
          <tt:Height>1440</tt:Height>
        </tt:Resolution>
        <tt:RateControl ConstantBitRate="false">
          <tt:FrameRateLimit>15</tt:FrameRateLimit>
          <tt:BitrateLimit>3000</tt:BitrateLimit>
        </tt:RateControl>
        <tt:Multicast>
          <tt:Address>
            <tt:Type>IPv4</tt:Type>
            <tt:IPv4Address>192.168.178.67</tt:IPv4Address>
          </tt:Address>
          <tt:Port>0</tt:Port>
          <tt:TTL>0</tt:TTL>
          <tt:AutoStart>false</tt:AutoStart>
        </tt:Multicast>
        <tt:Quality>50</tt:Quality>
      </tr2:VideoEncoder>
      <tr2:AudioEncoder token="G711A">
        <tt:Name>AudioMain</tt:Name>
        <tt:UseCount>2</tt:UseCount>
        <tt:Encoding>G711A</tt:Encoding>
        <tt:Multicast>
          <tt:Address>
            <tt:Type>IPv4</tt:Type>
            <tt:IPv4Address>192.168.178.67</tt:IPv4Address>
          </tt:Address>
          <tt:Port>80</tt:Port>
          <tt:TTL>1</tt:TTL>
          <tt:AutoStart>false</tt:AutoStart>
        </tt:Multicast>
        <tt:Bitrate>64000</tt:Bitrate>
        <tt:SampleRate>8000</tt:SampleRate>
      </tr2:AudioEncoder>
      <tr2:Analytics token="VideoAnalyticsToken">
        <tt:Name>VideoAnalyticsName</tt:Name>
        <tt:UseCount>3</tt:UseCount>
        <tt:AnalyticsEngineConfiguration>
          <tt:AnalyticsModule Type="tt:CellMotionEngine" Name="MyCellMotionModule">
            <tt:Parameters>
              <tt:SimpleItem Name="Sensitivity" Value="0" />
              <tt:ElementItem Name="Layout">
                <tt:CellLayout Rows="18" Columns="22">
                  <tt:Transformation>
                    <tt:Translate y="-1" x="-1" />
                    <tt:Scale y="9.99999997E-07" x="9.99999997E-07" />
                  </tt:Transformation>
                </tt:CellLayout>
              </tt:ElementItem>
            </tt:Parameters>
          </tt:AnalyticsModule>
        </tt:AnalyticsEngineConfiguration>
        <tt:RuleEngineConfiguration>
          <tt:Rule Type="tt:CellMotionDetector" Name="MyMotionDetectorRule">
            <tt:Parameters>
              <tt:SimpleItem Name="MinCount" Value="5" />
              <tt:SimpleItem Name="AlarmOnDelay" Value="100" />
              <tt:SimpleItem Name="AlarmOffDelay" Value="100" />
              <tt:SimpleItem Name="ActiveCells" Value="5wACQAAB7AA=" />
            </tt:Parameters>
          </tt:Rule>
        </tt:RuleEngineConfiguration>
      </tr2:Analytics>
      <tr2:PTZ token="ptz0">
        <tt:Name>ptz0</tt:Name>
        <tt:UseCount>2</tt:UseCount>
        <tt:NodeToken>ptz0</tt:NodeToken>
        <tt:DefaultAbsolutePantTiltPositionSpace>http://www.onvif.org/ver10/tptz/PanTiltSpaces/PositionGenericSpace</tt:DefaultAbsolutePantTiltPositionSpace>
        <tt:DefaultAbsoluteZoomPositionSpace>http://www.onvif.org/ver10/tptz/ZoomSpaces/PositionGenericSpace</tt:DefaultAbsoluteZoomPositionSpace>
        <tt:DefaultRelativePanTiltTranslationSpace>http://www.onvif.org/ver10/tptz/PanTiltSpaces/TranslationGenericSpace</tt:DefaultRelativePanTiltTranslationSpace>
        <tt:DefaultRelativeZoomTranslationSpace>http://www.onvif.org/ver10/tptz/ZoomSpaces/TranslationGenericSpace</tt:DefaultRelativeZoomTranslationSpace>
        <tt:DefaultContinuousPanTiltVelocitySpace>http://www.onvif.org/ver10/tptz/PanTiltSpaces/VelocityGenericSpace</tt:DefaultContinuousPanTiltVelocitySpace>
        <tt:DefaultContinuousZoomVelocitySpace>http://www.onvif.org/ver10/tptz/ZoomSpaces/VelocityGenericSpace</tt:DefaultContinuousZoomVelocitySpace>
        <tt:DefaultPTZSpeed>
          <tt:PanTilt x="1" y="1" space="http://www.onvif.org/ver10/tptz/PanTiltSpaces/GenericSpeedSpace" />
          <tt:Zoom x="1" space="http://www.onvif.org/ver10/tptz/ZoomSpaces/ZoomGenericSpeedSpace" />
        </tt:DefaultPTZSpeed>
        <tt:DefaultPTZTimeout>PT00H01M00S</tt:DefaultPTZTimeout>
        <tt:PanTiltLimits>
          <tt:Range>
            <tt:URI>http://www.onvif.org/ver10/tptz/PanTiltSpaces/PositionGenericSpace</tt:URI>
            <tt:XRange>
              <tt:Min>-1</tt:Min>
              <tt:Max>1</tt:Max>
            </tt:XRange>
            <tt:YRange>
              <tt:Min>-1</tt:Min>
              <tt:Max>1</tt:Max>
            </tt:YRange>
          </tt:Range>
        </tt:PanTiltLimits>
        <tt:ZoomLimits>
          <tt:Range>
            <tt:URI>http://www.onvif.org/ver10/tptz/ZoomSpaces/PositionGenericSpace</tt:URI>
            <tt:XRange>
              <tt:Min>-1</tt:Min>
              <tt:Max>1</tt:Max>
            </tt:XRange>
          </tt:Range>
        </tt:ZoomLimits>
      </tr2:PTZ>
      <tr2:AudioDecoder token="AudioMainToken">
        <tt:Name>AudioMainToken</tt:Name>
        <tt:UseCount>1</tt:UseCount>
      </tr2:AudioDecoder>
    </tr2:Configurations>
  </tr2:Profiles>
  <tr2:Profiles token="SubStream" fixed="true">
    <tr2:Name>SubStream</tr2:Name>
    <tr2:Configurations>
      <tr2:VideoSource token="VideoSourceMain">
        <tt:Name>VideoSourceMain</tt:Name>
        <tt:UseCount>2</tt:UseCount>
        <tt:SourceToken>VideoSourceMain</tt:SourceToken>
        <tt:Bounds x="0" y="0" width="640" height="360" />
      </tr2:VideoSource>
      <tr2:AudioSource token="AudioMainToken">
        <tt:Name>AudioMainName</tt:Name>
        <tt:UseCount>2</tt:UseCount>
        <tt:SourceToken>AudioMainSrcToken</tt:SourceToken>
      </tr2:AudioSource>
      <tr2:VideoEncoder token="VideoEncodeSub" GovLength="45" Profile="High">
        <tt:Name>VideoEncodeSub</tt:Name>
        <tt:UseCount>1</tt:UseCount>
        <tt:Encoding>H265</tt:Encoding>
        <tt:Resolution>
          <tt:Width>640</tt:Width>
          <tt:Height>360</tt:Height>
        </tt:Resolution>
        <tt:RateControl ConstantBitRate="false">
          <tt:FrameRateLimit>15</tt:FrameRateLimit>
          <tt:BitrateLimit>500</tt:BitrateLimit>
        </tt:RateControl>
        <tt:Multicast>
          <tt:Address>
            <tt:Type>IPv4</tt:Type>
            <tt:IPv4Address>192.168.178.67</tt:IPv4Address>
          </tt:Address>
          <tt:Port>0</tt:Port>
          <tt:TTL>0</tt:TTL>
          <tt:AutoStart>false</tt:AutoStart>
        </tt:Multicast>
        <tt:Quality>50</tt:Quality>
      </tr2:VideoEncoder>
      <tr2:AudioEncoder token="G711A">
        <tt:Name>AudioMain</tt:Name>
        <tt:UseCount>2</tt:UseCount>
        <tt:Encoding>G711A</tt:Encoding>
        <tt:Multicast>
          <tt:Address>
            <tt:Type>IPv4</tt:Type>
            <tt:IPv4Address>192.168.178.67</tt:IPv4Address>
          </tt:Address>
          <tt:Port>80</tt:Port>
          <tt:TTL>1</tt:TTL>
          <tt:AutoStart>false</tt:AutoStart>
        </tt:Multicast>
        <tt:Bitrate>64000</tt:Bitrate>
        <tt:SampleRate>8000</tt:SampleRate>
      </tr2:AudioEncoder>
      <tr2:Analytics token="VideoAnalyticsToken">
        <tt:Name>VideoAnalyticsName</tt:Name>
        <tt:UseCount>3</tt:UseCount>
        <tt:AnalyticsEngineConfiguration>
          <tt:AnalyticsModule Type="tt:CellMotionEngine" Name="MyCellMotionModule">
            <tt:Parameters>
              <tt:SimpleItem Name="Sensitivity" Value="80" />
              <tt:ElementItem Name="Layout">
                <tt:CellLayout Rows="18" Columns="22">
                  <tt:Transformation>
                    <tt:Translate y="-1" x="-1" />
                    <tt:Scale y="9.99999997E-07" x="9.99999997E-07" />
                  </tt:Transformation>
                </tt:CellLayout>
              </tt:ElementItem>
            </tt:Parameters>
          </tt:AnalyticsModule>
        </tt:AnalyticsEngineConfiguration>
        <tt:RuleEngineConfiguration>
          <tt:Rule Type="tt:CellMotionDetector" Name="MyMotionDetectorRule">
            <tt:Parameters>
              <tt:SimpleItem Name="MinCount" Value="5" />
              <tt:SimpleItem Name="AlarmOnDelay" Value="100" />
              <tt:SimpleItem Name="AlarmOffDelay" Value="100" />
              <tt:SimpleItem Name="ActiveCells" Value="5wACQAAB7AA=" />
            </tt:Parameters>
          </tt:Rule>
        </tt:RuleEngineConfiguration>
      </tr2:Analytics>
      <tr2:PTZ token="ptz0">
        <tt:Name>ptz0</tt:Name>
        <tt:UseCount>2</tt:UseCount>
        <tt:NodeToken>ptz0</tt:NodeToken>
        <tt:DefaultAbsolutePantTiltPositionSpace>http://www.onvif.org/ver10/tptz/PanTiltSpaces/PositionGenericSpace</tt:DefaultAbsolutePantTiltPositionSpace>
        <tt:DefaultAbsoluteZoomPositionSpace>http://www.onvif.org/ver10/tptz/ZoomSpaces/PositionGenericSpace</tt:DefaultAbsoluteZoomPositionSpace>
        <tt:DefaultRelativePanTiltTranslationSpace>http://www.onvif.org/ver10/tptz/PanTiltSpaces/TranslationGenericSpace</tt:DefaultRelativePanTiltTranslationSpace>
        <tt:DefaultRelativeZoomTranslationSpace>http://www.onvif.org/ver10/tptz/ZoomSpaces/TranslationGenericSpace</tt:DefaultRelativeZoomTranslationSpace>
        <tt:DefaultContinuousPanTiltVelocitySpace>http://www.onvif.org/ver10/tptz/PanTiltSpaces/VelocityGenericSpace</tt:DefaultContinuousPanTiltVelocitySpace>
        <tt:DefaultContinuousZoomVelocitySpace>http://www.onvif.org/ver10/tptz/ZoomSpaces/VelocityGenericSpace</tt:DefaultContinuousZoomVelocitySpace>
        <tt:DefaultPTZSpeed>
          <tt:PanTilt x="1" y="1" space="http://www.onvif.org/ver10/tptz/PanTiltSpaces/GenericSpeedSpace" />
          <tt:Zoom x="1" space="http://www.onvif.org/ver10/tptz/ZoomSpaces/ZoomGenericSpeedSpace" />
        </tt:DefaultPTZSpeed>
        <tt:DefaultPTZTimeout>PT00H01M00S</tt:DefaultPTZTimeout>
        <tt:PanTiltLimits>
          <tt:Range>
            <tt:URI>http://www.onvif.org/ver10/tptz/PanTiltSpaces/PositionGenericSpace</tt:URI>
            <tt:XRange>
              <tt:Min>-1</tt:Min>
              <tt:Max>1</tt:Max>
            </tt:XRange>
            <tt:YRange>
              <tt:Min>-1</tt:Min>
              <tt:Max>1</tt:Max>
            </tt:YRange>
          </tt:Range>
        </tt:PanTiltLimits>
        <tt:ZoomLimits>
          <tt:Range>
            <tt:URI>http://www.onvif.org/ver10/tptz/ZoomSpaces/PositionGenericSpace</tt:URI>
            <tt:XRange>
              <tt:Min>-1</tt:Min>
              <tt:Max>1</tt:Max>
            </tt:XRange>
          </tt:Range>
        </tt:ZoomLimits>
      </tr2:PTZ>
      <tr2:AudioDecoder token="AudioMainToken">
        <tt:Name>AudioMainToken</tt:Name>
        <tt:UseCount>1</tt:UseCount>
      </tr2:AudioDecoder>
    </tr2:Configurations>
  </tr2:Profiles>
</tr2:GetProfilesResponse>
```

</details>

<details><summary><code>GetStreamUri (RtspUnicast, MainStream)</code></summary>

```xml
<tr2:GetStreamUriResponse xmlns:tr2="http://www.onvif.org/ver20/media/wsdl">
  <tr2:Uri>rtsp://192.168.178.67:554/stream0?username=admin&amp;password=<redacted></tr2:Uri>
</tr2:GetStreamUriResponse>
```

</details>

<details><summary><code>GetSnapshotUri (MainStream)</code></summary>

```xml
<tr2:GetSnapshotUriResponse xmlns:tr2="http://www.onvif.org/ver20/media/wsdl">
  <tr2:Uri>http://192.168.178.67:80/cgi-bin/snapshot.cgi?stream=1&amp;username=admin&amp;password=<redacted></tr2:Uri>
</tr2:GetSnapshotUriResponse>
```

</details>

<details><summary><code>GetStreamUri (RtspUnicast, SubStream)</code></summary>

```xml
<tr2:GetStreamUriResponse xmlns:tr2="http://www.onvif.org/ver20/media/wsdl">
  <tr2:Uri>rtsp://192.168.178.67:554/stream1?username=admin&amp;password=<redacted></tr2:Uri>
</tr2:GetStreamUriResponse>
```

</details>

<details><summary><code>GetSnapshotUri (SubStream)</code></summary>

```xml
<tr2:GetSnapshotUriResponse xmlns:tr2="http://www.onvif.org/ver20/media/wsdl">
  <tr2:Uri>http://192.168.178.67:80/cgi-bin/snapshot.cgi?stream=2&amp;username=admin&amp;password=<redacted></tr2:Uri>
</tr2:GetSnapshotUriResponse>
```

</details>

<details><summary><code>GetVideoSourceConfigurations</code></summary>

```xml
<tr2:GetVideoSourceConfigurationsResponse xmlns:tr2="http://www.onvif.org/ver20/media/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
  <tr2:Configurations token="VideoSourceMain">
    <tt:Name>VideoSourceMain</tt:Name>
    <tt:UseCount>2</tt:UseCount>
    <tt:SourceToken>VideoSourceMain</tt:SourceToken>
    <tt:Bounds x="0" y="0" width="2560" height="1440" />
  </tr2:Configurations>
</tr2:GetVideoSourceConfigurationsResponse>
```

</details>

<details><summary><code>GetVideoSourceConfigurationOptions (VideoSourceMain)</code></summary>

```xml
<tr2:GetVideoSourceConfigurationOptionsResponse xmlns:tr2="http://www.onvif.org/ver20/media/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
  <tr2:Options>
    <tt:BoundsRange>
      <tt:XRange>
        <tt:Min>0</tt:Min>
        <tt:Max>3840</tt:Max>
      </tt:XRange>
      <tt:YRange>
        <tt:Min>0</tt:Min>
        <tt:Max>2160</tt:Max>
      </tt:YRange>
      <tt:WidthRange>
        <tt:Min>0</tt:Min>
        <tt:Max>3840</tt:Max>
      </tt:WidthRange>
      <tt:HeightRange>
        <tt:Min>0</tt:Min>
        <tt:Max>2160</tt:Max>
      </tt:HeightRange>
    </tt:BoundsRange>
    <tt:VideoSourceTokensAvailable>VideoSourceMain</tt:VideoSourceTokensAvailable>
  </tr2:Options>
</tr2:GetVideoSourceConfigurationOptionsResponse>
```

</details>

<details><summary><code>GetVideoSourceModes (VideoSourceMain)</code></summary>

```xml
<tr2:GetVideoSourceModesResponse xmlns:tr2="http://www.onvif.org/ver20/media/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
  <tr2:VideoSourceModes token="PAL" Enabled="true">
    <tr2:MaxFramerate>30</tr2:MaxFramerate>
    <tr2:MaxResolution>
      <tt:Width>2160</tt:Width>
      <tt:Height>3140</tt:Height>
    </tr2:MaxResolution>
    <tr2:Encodings>H264 H265</tr2:Encodings>
    <tr2:Reboot>true</tr2:Reboot>
    <tr2:Description>Video Mode
</tr2:Description>
  </tr2:VideoSourceModes>
  <tr2:VideoSourceModes token="NTSC" Enabled="true">
    <tr2:MaxFramerate>30</tr2:MaxFramerate>
    <tr2:MaxResolution>
      <tt:Width>2160</tt:Width>
      <tt:Height>3140</tt:Height>
    </tr2:MaxResolution>
    <tr2:Encodings>H264 H265</tr2:Encodings>
    <tr2:Reboot>true</tr2:Reboot>
    <tr2:Description>Video Mode
</tr2:Description>
  </tr2:VideoSourceModes>
</tr2:GetVideoSourceModesResponse>
```

</details>

<details><summary><code>GetVideoEncoderConfigurations</code></summary>

```xml
<tr2:GetVideoEncoderConfigurationsResponse xmlns:tr2="http://www.onvif.org/ver20/media/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
  <tr2:Configurations token="VideoEncodeMain" GovLength="45" Profile="High">
    <tt:Name>VideoEncodeMain</tt:Name>
    <tt:UseCount>1</tt:UseCount>
    <tt:Encoding>H264</tt:Encoding>
    <tt:Resolution>
      <tt:Width>2560</tt:Width>
      <tt:Height>1440</tt:Height>
    </tt:Resolution>
    <tt:RateControl ConstantBitRate="false">
      <tt:FrameRateLimit>15</tt:FrameRateLimit>
      <tt:BitrateLimit>3000</tt:BitrateLimit>
    </tt:RateControl>
    <tt:Multicast>
      <tt:Address>
        <tt:Type>IPv4</tt:Type>
        <tt:IPv4Address>192.168.178.67</tt:IPv4Address>
      </tt:Address>
      <tt:Port>0</tt:Port>
      <tt:TTL>0</tt:TTL>
      <tt:AutoStart>false</tt:AutoStart>
    </tt:Multicast>
    <tt:Quality>50</tt:Quality>
  </tr2:Configurations>
  <tr2:Configurations token="VideoEncodeSub" GovLength="45" Profile="High">
    <tt:Name>VideoEncodeSub</tt:Name>
    <tt:UseCount>1</tt:UseCount>
    <tt:Encoding>H265</tt:Encoding>
    <tt:Resolution>
      <tt:Width>640</tt:Width>
      <tt:Height>360</tt:Height>
    </tt:Resolution>
    <tt:RateControl ConstantBitRate="false">
      <tt:FrameRateLimit>15</tt:FrameRateLimit>
      <tt:BitrateLimit>500</tt:BitrateLimit>
    </tt:RateControl>
    <tt:Multicast>
      <tt:Address>
        <tt:Type>IPv4</tt:Type>
        <tt:IPv4Address>192.168.178.67</tt:IPv4Address>
      </tt:Address>
      <tt:Port>0</tt:Port>
      <tt:TTL>0</tt:TTL>
      <tt:AutoStart>false</tt:AutoStart>
    </tt:Multicast>
    <tt:Quality>50</tt:Quality>
  </tr2:Configurations>
</tr2:GetVideoEncoderConfigurationsResponse>
```

</details>

<details><summary><code>GetVideoEncoderConfigurationOptions (VideoEncodeMain)</code></summary>

```xml
<tr2:GetVideoEncoderConfigurationOptionsResponse xmlns:tr2="http://www.onvif.org/ver20/media/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
  <tr2:Options GovLengthRange="200 199 198 197 196 195 194 193 192 191 190 189 188 187 186 185 184 183 182 181 180 179 178 177 176 175 174 173 172 171 170 169 168 167 166 165 164 163 162 161 160 159 158 157 156 155 154 153 152 151 150 149 148 147 146 145 144 143 142 141 140 139 138 137 136 135 134 133 132 131 130 129 128 127 126 125 124 123 122 121 120 119 118 117 116 115 114 113 112 111 110 109 108 107 106 105 104 103 102 101 100 99 98 97 96 95 94 93 92 91 90 89 88 87 86 85 84 83 82 81 80 79 78 77 76 75 74 73 72 71 70 69 68 67 66 65 64 63 62 61 60 59 58 57 56 55 54 53 52 51 50 49 48 47 46 45 44 43 42 41 40 39 38 37 36 35 34 33 32 31 30 29 28 27 26 25 24 23 22 21 20 19 18 17 16 15 14 13 12 11 10 9 8 7 6 5 4 3 2 1" FrameRatesSupported="30 29 28 27 26 25 24 23 22 21 20 19 18 17 16 15 14 13 12 11 10 9 8 7 6 5 4 3 2 1" ProfilesSupported="Main" ConstantBitRateSupported="true">
    <tt:Encoding>H264</tt:Encoding>
    <tt:QualityRange>
      <tt:Min>10</tt:Min>
      <tt:Max>100</tt:Max>
    </tt:QualityRange>
    <tt:ResolutionsAvailable>
      <tt:Width>2560</tt:Width>
      <tt:Height>1440</tt:Height>
      <tt:FrameRateRange>
        <tt:Min>8</tt:Min>
        <tt:Max>25</tt:Max>
      </tt:FrameRateRange>
      <tt:BitrateRange>
        <tt:Min>512</tt:Min>
        <tt:Max>8192</tt:Max>
      </tt:BitrateRange>
    </tt:ResolutionsAvailable>
    <tt:ResolutionsAvailable>
      <tt:Width>2304</tt:Width>
      <tt:Height>1296</tt:Height>
      <tt:FrameRateRange>
        <tt:Min>8</tt:Min>
        <tt:Max>30</tt:Max>
      </tt:FrameRateRange>
      <tt:BitrateRange>
        <tt:Min>512</tt:Min>
        <tt:Max>8192</tt:Max>
      </tt:BitrateRange>
    </tt:ResolutionsAvailable>
    <tt:ResolutionsAvailable>
      <tt:Width>1920</tt:Width>
      <tt:Height>1080</tt:Height>
      <tt:FrameRateRange>
        <tt:Min>8</tt:Min>
        <tt:Max>30</tt:Max>
      </tt:FrameRateRange>
      <tt:BitrateRange>
        <tt:Min>512</tt:Min>
        <tt:Max>9216</tt:Max>
      </tt:BitrateRange>
    </tt:ResolutionsAvailable>
    <tt:ResolutionsAvailable>
      <tt:Width>1280</tt:Width>
      <tt:Height>720</tt:Height>
      <tt:FrameRateRange>
        <tt:Min>8</tt:Min>
        <tt:Max>30</tt:Max>
      </tt:FrameRateRange>
      <tt:BitrateRange>
        <tt:Min>512</tt:Min>
        <tt:Max>6144</tt:Max>
      </tt:BitrateRange>
    </tt:ResolutionsAvailable>
    <tt:BitrateRange>
      <tt:Min>512</tt:Min>
      <tt:Max>9216</tt:Max>
    </tt:BitrateRange>
    <tt:GovLengthRange>1-200</tt:GovLengthRange>
    <tt:FrameRatesSupported>1-30</tt:FrameRatesSupported>
    <tt:ProfilesSupported>Main</tt:ProfilesSupported>
    <tt:ConstantBitRateSupported>true</tt:ConstantBitRateSupported>
  </tr2:Options>
  <tr2:Options GovLengthRange="200 199 198 197 196 195 194 193 192 191 190 189 188 187 186 185 184 183 182 181 180 179 178 177 176 175 174 173 172 171 170 169 168 167 166 165 164 163 162 161 160 159 158 157 156 155 154 153 152 151 150 149 148 147 146 145 144 143 142 141 140 139 138 137 136 135 134 133 132 131 130 129 128 127 126 125 124 123 122 121 120 119 118 117 116 115 114 113 112 111 110 109 108 107 106 105 104 103 102 101 100 99 98 97 96 95 94 93 92 91 90 89 88 87 86 85 84 83 82 81 80 79 78 77 76 75 74 73 72 71 70 69 68 67 66 65 64 63 62 61 60 59 58 57 56 55 54 53 52 51 50 49 48 47 46 45 44 43 42 41 40 39 38 37 36 35 34 33 32 31 30 29 28 27 26 25 24 23 22 21 20 19 18 17 16 15 14 13 12 11 10 9 8 7 6 5 4 3 2 1" FrameRatesSupported="30 29 28 27 26 25 24 23 22 21 20 19 18 17 16 15 14 13 12 11 10 9 8 7 6 5 4 3 2 1" ProfilesSupported="Main" ConstantBitRateSupported="true">
    <tt:Encoding>H265</tt:Encoding>
    <tt:QualityRange>
      <tt:Min>10</tt:Min>
      <tt:Max>100</tt:Max>
    </tt:QualityRange>
    <tt:ResolutionsAvailable>
      <tt:Width>2560</tt:Width>
      <tt:Height>1440</tt:Height>
      <tt:FrameRateRange>
        <tt:Min>8</tt:Min>
        <tt:Max>25</tt:Max>
      </tt:FrameRateRange>
      <tt:BitrateRange>
        <tt:Min>512</tt:Min>
        <tt:Max>6144</tt:Max>
      </tt:BitrateRange>
    </tt:ResolutionsAvailable>
    <tt:ResolutionsAvailable>
      <tt:Width>2304</tt:Width>
      <tt:Height>1296</tt:Height>
      <tt:FrameRateRange>
        <tt:Min>8</tt:Min>
        <tt:Max>30</tt:Max>
      </tt:FrameRateRange>
      <tt:BitrateRange>
        <tt:Min>512</tt:Min>
        <tt:Max>6144</tt:Max>
      </tt:BitrateRange>
    </tt:ResolutionsAvailable>
    <tt:ResolutionsAvailable>
      <tt:Width>1920</tt:Width>
      <tt:Height>1080</tt:Height>
      <tt:FrameRateRange>
        <tt:Min>8</tt:Min>
        <tt:Max>30</tt:Max>
      </tt:FrameRateRange>
      <tt:BitrateRange>
        <tt:Min>224</tt:Min>
        <tt:Max>8192</tt:Max>
      </tt:BitrateRange>
    </tt:ResolutionsAvailable>
    <tt:ResolutionsAvailable>
      <tt:Width>1280</tt:Width>
      <tt:Height>720</tt:Height>
      <tt:FrameRateRange>
        <tt:Min>8</tt:Min>
        <tt:Max>30</tt:Max>
      </tt:FrameRateRange>
      <tt:BitrateRange>
        <tt:Min>128</tt:Min>
        <tt:Max>5120</tt:Max>
      </tt:BitrateRange>
    </tt:ResolutionsAvailable>
    <tt:BitrateRange>
      <tt:Min>128</tt:Min>
      <tt:Max>8192</tt:Max>
    </tt:BitrateRange>
    <tt:GovLengthRange>1-200</tt:GovLengthRange>
    <tt:FrameRatesSupported>1-30</tt:FrameRatesSupported>
    <tt:ProfilesSupported>Main</tt:ProfilesSupported>
    <tt:ConstantBitRateSupported>true</tt:ConstantBitRateSupported>
  </tr2:Options>
</tr2:GetVideoEncoderConfigurationOptionsResponse>
```

</details>

<details><summary><code>GetVideoEncoderConfigurationOptions (VideoEncodeSub)</code></summary>

```xml
<tr2:GetVideoEncoderConfigurationOptionsResponse xmlns:tr2="http://www.onvif.org/ver20/media/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
  <tr2:Options GovLengthRange="200 199 198 197 196 195 194 193 192 191 190 189 188 187 186 185 184 183 182 181 180 179 178 177 176 175 174 173 172 171 170 169 168 167 166 165 164 163 162 161 160 159 158 157 156 155 154 153 152 151 150 149 148 147 146 145 144 143 142 141 140 139 138 137 136 135 134 133 132 131 130 129 128 127 126 125 124 123 122 121 120 119 118 117 116 115 114 113 112 111 110 109 108 107 106 105 104 103 102 101 100 99 98 97 96 95 94 93 92 91 90 89 88 87 86 85 84 83 82 81 80 79 78 77 76 75 74 73 72 71 70 69 68 67 66 65 64 63 62 61 60 59 58 57 56 55 54 53 52 51 50 49 48 47 46 45 44 43 42 41 40 39 38 37 36 35 34 33 32 31 30 29 28 27 26 25 24 23 22 21 20 19 18 17 16 15 14 13 12 11 10 9 8 7 6 5 4 3 2 1" FrameRatesSupported="30 29 28 27 26 25 24 23 22 21 20 19 18 17 16 15 14 13 12 11 10 9 8 7 6 5 4 3 2 1" ProfilesSupported="Main" ConstantBitRateSupported="true">
    <tt:Encoding>H264</tt:Encoding>
    <tt:QualityRange>
      <tt:Min>10</tt:Min>
      <tt:Max>100</tt:Max>
    </tt:QualityRange>
    <tt:ResolutionsAvailable>
      <tt:Width>640</tt:Width>
      <tt:Height>360</tt:Height>
      <tt:FrameRateRange>
        <tt:Min>8</tt:Min>
        <tt:Max>30</tt:Max>
      </tt:FrameRateRange>
      <tt:BitrateRange>
        <tt:Min>64</tt:Min>
        <tt:Max>2048</tt:Max>
      </tt:BitrateRange>
    </tt:ResolutionsAvailable>
    <tt:ResolutionsAvailable>
      <tt:Width>480</tt:Width>
      <tt:Height>360</tt:Height>
      <tt:FrameRateRange>
        <tt:Min>8</tt:Min>
        <tt:Max>30</tt:Max>
      </tt:FrameRateRange>
      <tt:BitrateRange>
        <tt:Min>64</tt:Min>
        <tt:Max>2048</tt:Max>
      </tt:BitrateRange>
    </tt:ResolutionsAvailable>
    <tt:ResolutionsAvailable>
      <tt:Width>352</tt:Width>
      <tt:Height>288</tt:Height>
      <tt:FrameRateRange>
        <tt:Min>8</tt:Min>
        <tt:Max>30</tt:Max>
      </tt:FrameRateRange>
      <tt:BitrateRange>
        <tt:Min>64</tt:Min>
        <tt:Max>2048</tt:Max>
      </tt:BitrateRange>
    </tt:ResolutionsAvailable>
    <tt:BitrateRange>
      <tt:Min>64</tt:Min>
      <tt:Max>2048</tt:Max>
    </tt:BitrateRange>
    <tt:GovLengthRange>1-200</tt:GovLengthRange>
    <tt:FrameRatesSupported>1-30</tt:FrameRatesSupported>
    <tt:ProfilesSupported>Main</tt:ProfilesSupported>
    <tt:ConstantBitRateSupported>true</tt:ConstantBitRateSupported>
  </tr2:Options>
  <tr2:Options GovLengthRange="200 199 198 197 196 195 194 193 192 191 190 189 188 187 186 185 184 183 182 181 180 179 178 177 176 175 174 173 172 171 170 169 168 167 166 165 164 163 162 161 160 159 158 157 156 155 154 153 152 151 150 149 148 147 146 145 144 143 142 141 140 139 138 137 136 135 134 133 132 131 130 129 128 127 126 125 124 123 122 121 120 119 118 117 116 115 114 113 112 111 110 109 108 107 106 105 104 103 102 101 100 99 98 97 96 95 94 93 92 91 90 89 88 87 86 85 84 83 82 81 80 79 78 77 76 75 74 73 72 71 70 69 68 67 66 65 64 63 62 61 60 59 58 57 56 55 54 53 52 51 50 49 48 47 46 45 44 43 42 41 40 39 38 37 36 35 34 33 32 31 30 29 28 27 26 25 24 23 22 21 20 19 18 17 16 15 14 13 12 11 10 9 8 7 6 5 4 3 2 1" FrameRatesSupported="30 29 28 27 26 25 24 23 22 21 20 19 18 17 16 15 14 13 12 11 10 9 8 7 6 5 4 3 2 1" ProfilesSupported="Main" ConstantBitRateSupported="true">
    <tt:Encoding>H265</tt:Encoding>
    <tt:QualityRange>
      <tt:Min>10</tt:Min>
      <tt:Max>100</tt:Max>
    </tt:QualityRange>
    <tt:ResolutionsAvailable>
      <tt:Width>640</tt:Width>
      <tt:Height>360</tt:Height>
      <tt:FrameRateRange>
        <tt:Min>8</tt:Min>
        <tt:Max>30</tt:Max>
      </tt:FrameRateRange>
      <tt:BitrateRange>
        <tt:Min>64</tt:Min>
        <tt:Max>1024</tt:Max>
      </tt:BitrateRange>
    </tt:ResolutionsAvailable>
    <tt:ResolutionsAvailable>
      <tt:Width>480</tt:Width>
      <tt:Height>360</tt:Height>
      <tt:FrameRateRange>
        <tt:Min>8</tt:Min>
        <tt:Max>30</tt:Max>
      </tt:FrameRateRange>
      <tt:BitrateRange>
        <tt:Min>64</tt:Min>
        <tt:Max>1024</tt:Max>
      </tt:BitrateRange>
    </tt:ResolutionsAvailable>
    <tt:ResolutionsAvailable>
      <tt:Width>352</tt:Width>
      <tt:Height>288</tt:Height>
      <tt:FrameRateRange>
        <tt:Min>8</tt:Min>
        <tt:Max>30</tt:Max>
      </tt:FrameRateRange>
      <tt:BitrateRange>
        <tt:Min>64</tt:Min>
        <tt:Max>2048</tt:Max>
      </tt:BitrateRange>
    </tt:ResolutionsAvailable>
    <tt:BitrateRange>
      <tt:Min>64</tt:Min>
      <tt:Max>2048</tt:Max>
    </tt:BitrateRange>
    <tt:GovLengthRange>1-200</tt:GovLengthRange>
    <tt:FrameRatesSupported>1-30</tt:FrameRatesSupported>
    <tt:ProfilesSupported>Main</tt:ProfilesSupported>
    <tt:ConstantBitRateSupported>true</tt:ConstantBitRateSupported>
  </tr2:Options>
</tr2:GetVideoEncoderConfigurationOptionsResponse>
```

</details>

<details><summary><code>GetAudioSourceConfigurations</code></summary>

```xml
<tr2:GetAudioSourceConfigurationsResponse xmlns:tr2="http://www.onvif.org/ver20/media/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
  <tr2:Configurations token="AudioMainToken">
    <tt:Name>AudioMainName</tt:Name>
    <tt:UseCount>1</tt:UseCount>
    <tt:SourceToken>AudioMainSrcToken</tt:SourceToken>
  </tr2:Configurations>
</tr2:GetAudioSourceConfigurationsResponse>
```

</details>

<details><summary><code>GetAudioEncoderConfigurations</code></summary>

```xml
<tr2:GetAudioEncoderConfigurationsResponse xmlns:tr2="http://www.onvif.org/ver20/media/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
  <tr2:Configurations token="G711A">
    <tt:Name>AudioMain</tt:Name>
    <tt:UseCount>2</tt:UseCount>
    <tt:Encoding>G711A</tt:Encoding>
    <tt:Multicast>
      <tt:Address>
        <tt:Type>IPv4</tt:Type>
        <tt:IPv4Address>http://192.168.178.67:80/onvif/services</tt:IPv4Address>
      </tt:Address>
      <tt:Port>80</tt:Port>
      <tt:TTL>1</tt:TTL>
      <tt:AutoStart>false</tt:AutoStart>
    </tt:Multicast>
    <tt:Bitrate>64000</tt:Bitrate>
    <tt:SampleRate>8000</tt:SampleRate>
  </tr2:Configurations>
</tr2:GetAudioEncoderConfigurationsResponse>
```

</details>

<details><summary><code>GetAudioEncoderConfigurationOptions (G711A)</code></summary>

```xml
<tr2:GetAudioEncoderConfigurationOptionsResponse xmlns:tr2="http://www.onvif.org/ver20/media/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
  <tr2:Options>
    <tt:Encoding>G711A</tt:Encoding>
    <tt:BitrateList>
      <tt:Items>64</tt:Items>
    </tt:BitrateList>
    <tt:SampleRateList>
      <tt:Items>8</tt:Items>
      <tt:Items>16</tt:Items>
    </tt:SampleRateList>
  </tr2:Options>
</tr2:GetAudioEncoderConfigurationOptionsResponse>
```

</details>

<details><summary><code>GetMetadataConfigurations</code></summary>

```xml
<tr2:GetMetadataConfigurationsResponse xmlns:tr2="http://www.onvif.org/ver20/media/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
  <tr2:Configurations>
    <tt:Name>metaData</tt:Name>
    <tt:UseCount>0</tt:UseCount>
    <tt:PTZStatus>
      <tt:Status>true</tt:Status>
      <tt:Position>true</tt:Position>
    </tt:PTZStatus>
    <tt:Analytics>true</tt:Analytics>
    <tt:Multicast>
      <tt:Address>
        <tt:Type>IPv4</tt:Type>
        <tt:IPv4Address>0.0.0.0</tt:IPv4Address>
      </tt:Address>
      <tt:Port>0</tt:Port>
      <tt:TTL>0</tt:TTL>
      <tt:AutoStart>false</tt:AutoStart>
    </tt:Multicast>
    <tt:SessionTimeout>PT00H00M00.005S</tt:SessionTimeout>
    <tt:AnalyticsEngineConfiguration />
  </tr2:Configurations>
</tr2:GetMetadataConfigurationsResponse>
```

</details>

<details><summary><code>GetOSDs</code></summary>

```xml
<tr2:GetOSDsResponse xmlns:tr2="http://www.onvif.org/ver20/media/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
  <tr2:OSDs token="osd_title">
    <tt:VideoSourceConfigurationToken>VideoSourceMain</tt:VideoSourceConfigurationToken>
    <tt:Type>Text</tt:Type>
    <tt:Position>
      <tt:Type>UpperLeft</tt:Type>
    </tt:Position>
    <tt:TextString>
      <tt:Type>Plain</tt:Type>
      <tt:PlainText>Camera</tt:PlainText>
    </tt:TextString>
  </tr2:OSDs>
  <tr2:OSDs token="osd_time">
    <tt:VideoSourceConfigurationToken>VideoSourceMain</tt:VideoSourceConfigurationToken>
    <tt:Type>Text</tt:Type>
    <tt:Position>
      <tt:Type>LowerRight</tt:Type>
    </tt:Position>
    <tt:TextString>
      <tt:Type>DateAndTime</tt:Type>
      <tt:DateFormat>MM/dd/yyyy</tt:DateFormat>
      <tt:TimeFormat>HH:mm:ss</tt:TimeFormat>
    </tt:TextString>
  </tr2:OSDs>
</tr2:GetOSDsResponse>
```

</details>

<details><summary><code>GetOSDOptions (VideoSourceMain)</code></summary>

```xml
<tr2:GetOSDOptionsResponse xmlns:tr2="http://www.onvif.org/ver20/media/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
  <tr2:OSDOptions>
    <tt:MaximumNumberOfOSDs Total="2" Image="0" PlainText="1" Date="0" Time="0" DateAndTime="1" />
    <tt:Type>Text</tt:Type>
    <tt:PositionOption>UpperLeft</tt:PositionOption>
    <tt:PositionOption>UpperRight</tt:PositionOption>
    <tt:PositionOption>LowerLeft</tt:PositionOption>
    <tt:PositionOption>LowerRight</tt:PositionOption>
    <tt:TextOption>
      <tt:Type>Plain</tt:Type>
      <tt:Type>DateAndTime</tt:Type>
      <tt:DateFormat>MM/dd/yyyy</tt:DateFormat>
      <tt:DateFormat>dd/MM/yyyy</tt:DateFormat>
      <tt:DateFormat>yyyy/MM/dd</tt:DateFormat>
      <tt:DateFormat>yyyy-MM-dd</tt:DateFormat>
      <tt:DateFormat>yy/MM/dd</tt:DateFormat>
      <tt:DateFormat>yy/MM/dd</tt:DateFormat>
      <tt:DateFormat>dd-MM-yyyy</tt:DateFormat>
      <tt:DateFormat>MM-dd-yyyy</tt:DateFormat>
      <tt:TimeFormat>HH:mm:ss</tt:TimeFormat>
    </tt:TextOption>
  </tr2:OSDOptions>
</tr2:GetOSDOptionsResponse>
```

</details>

### Imaging

<details><summary><code>GetImagingSettings (VideoSourceMain)</code></summary>

```xml
<timg:GetImagingSettingsResponse xmlns:timg="http://www.onvif.org/ver20/imaging/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
  <timg:ImagingSettings>
    <tt:BacklightCompensation>
      <tt:Mode>OFF</tt:Mode>
      <tt:Level>0</tt:Level>
    </tt:BacklightCompensation>
    <tt:Brightness>128</tt:Brightness>
    <tt:ColorSaturation>128</tt:ColorSaturation>
    <tt:Contrast>128</tt:Contrast>
    <tt:Exposure>
      <tt:Mode>AUTO</tt:Mode>
      <tt:MinExposureTime>125</tt:MinExposureTime>
      <tt:MaxExposureTime>20000</tt:MaxExposureTime>
      <tt:ExposureTime>100000</tt:ExposureTime>
    </tt:Exposure>
    <tt:Focus>
      <tt:AutoFocusMode>MANUAL</tt:AutoFocusMode>
      <tt:DefaultSpeed>0</tt:DefaultSpeed>
      <tt:NearLimit>1</tt:NearLimit>
      <tt:FarLimit>10</tt:FarLimit>
    </tt:Focus>
    <tt:IrCutFilter>ON</tt:IrCutFilter>
    <tt:Sharpness>128</tt:Sharpness>
    <tt:WideDynamicRange>
      <tt:Mode>OFF</tt:Mode>
      <tt:Level>128</tt:Level>
    </tt:WideDynamicRange>
    <tt:WhiteBalance>
      <tt:Mode>AUTO</tt:Mode>
      <tt:CrGain>128</tt:CrGain>
      <tt:CbGain>128</tt:CbGain>
    </tt:WhiteBalance>
    <tt:Extension>
      <tt:Extension>
        <tt:Extension>
          <tt:Defogging>
            <tt:Mode>OFF</tt:Mode>
            <tt:Level>0.501960814</tt:Level>
          </tt:Defogging>
          <tt:NoiseReduction>
            <tt:Level>0.501960814</tt:Level>
          </tt:NoiseReduction>
        </tt:Extension>
      </tt:Extension>
    </tt:Extension>
  </timg:ImagingSettings>
</timg:GetImagingSettingsResponse>
```

</details>

<details><summary><code>GetOptions (VideoSourceMain)</code></summary>

```xml
<timg:GetOptionsResponse xmlns:timg="http://www.onvif.org/ver20/imaging/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
  <timg:ImagingOptions>
    <tt:BacklightCompensation>
      <tt:Mode>ON</tt:Mode>
      <tt:Level>
        <tt:Min>0</tt:Min>
        <tt:Max>255</tt:Max>
      </tt:Level>
    </tt:BacklightCompensation>
    <tt:Brightness>
      <tt:Min>1</tt:Min>
      <tt:Max>255</tt:Max>
    </tt:Brightness>
    <tt:ColorSaturation>
      <tt:Min>1</tt:Min>
      <tt:Max>255</tt:Max>
    </tt:ColorSaturation>
    <tt:Contrast>
      <tt:Min>1</tt:Min>
      <tt:Max>255</tt:Max>
    </tt:Contrast>
    <tt:Exposure>
      <tt:Mode>AUTO</tt:Mode>
      <tt:Mode>MANUAL</tt:Mode>
      <tt:ExposureTime>
        <tt:Min>100</tt:Min>
        <tt:Max>100000</tt:Max>
      </tt:ExposureTime>
    </tt:Exposure>
    <tt:Focus>
      <tt:AutoFocusModes>MANUAL</tt:AutoFocusModes>
      <tt:DefaultSpeed>
        <tt:Min>1</tt:Min>
        <tt:Max>10</tt:Max>
      </tt:DefaultSpeed>
    </tt:Focus>
    <tt:IrCutFilterModes>ON</tt:IrCutFilterModes>
    <tt:IrCutFilterModes>OFF</tt:IrCutFilterModes>
    <tt:IrCutFilterModes>AUTO</tt:IrCutFilterModes>
    <tt:Sharpness>
      <tt:Min>1</tt:Min>
      <tt:Max>255</tt:Max>
    </tt:Sharpness>
    <tt:WideDynamicRange>
      <tt:Mode>OFF</tt:Mode>
      <tt:Mode>ON</tt:Mode>
      <tt:Level>
        <tt:Min>0</tt:Min>
        <tt:Max>255</tt:Max>
      </tt:Level>
    </tt:WideDynamicRange>
    <tt:WhiteBalance>
      <tt:Mode>AUTO</tt:Mode>
      <tt:Mode>MANUAL</tt:Mode>
      <tt:YrGain>
        <tt:Min>0</tt:Min>
        <tt:Max>255</tt:Max>
      </tt:YrGain>
      <tt:YbGain>
        <tt:Min>0</tt:Min>
        <tt:Max>255</tt:Max>
      </tt:YbGain>
    </tt:WhiteBalance>
    <tt:Extension>
      <tt:Extension>
        <tt:Extension>
          <tt:DefoggingOptions>
            <tt:Mode>OFF</tt:Mode>
            <tt:Mode>ON</tt:Mode>
            <tt:Level>true</tt:Level>
          </tt:DefoggingOptions>
          <tt:NoiseReductionOptions>
            <tt:Level>true</tt:Level>
          </tt:NoiseReductionOptions>
        </tt:Extension>
      </tt:Extension>
    </tt:Extension>
  </timg:ImagingOptions>
</timg:GetOptionsResponse>
```

</details>

<details><summary><code>GetMoveOptions (VideoSourceMain)</code></summary>

```xml
<timg:GetMoveOptionsResponse xmlns:timg="http://www.onvif.org/ver20/imaging/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
  <timg:MoveOptions>
    <tt:Continuous>
      <tt:Speed>
        <tt:Min>1</tt:Min>
        <tt:Max>10</tt:Max>
      </tt:Speed>
    </tt:Continuous>
  </timg:MoveOptions>
</timg:GetMoveOptionsResponse>
```

</details>

<details><summary><code>GetStatus (VideoSourceMain)</code></summary>

```xml
<timg:GetStatusResponse xmlns:timg="http://www.onvif.org/ver20/imaging/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
  <timg:Status>
    <tt:FocusStatus20>
      <tt:Position>0</tt:Position>
      <tt:MoveStatus>IDLE</tt:MoveStatus>
    </tt:FocusStatus20>
  </timg:Status>
</timg:GetStatusResponse>
```

</details>

### Events

<details><summary><code>GetServiceCapabilities</code></summary>

```xml
<tev:GetServiceCapabilitiesResponse xmlns:tev="http://www.onvif.org/ver10/events/wsdl">
  <tev:Capabilities WSSubscriptionPolicySupport="true" WSPullPointSupport="true" WSPausableSubscriptionManagerInterfaceSupport="true" MaxNotificationProducers="10" MaxPullPoints="10" PersistentNotificationStorage="false" />
</tev:GetServiceCapabilitiesResponse>
```

</details>

<details><summary><code>GetEventProperties</code></summary>

```xml
<tev:GetEventPropertiesResponse xmlns:tev="http://www.onvif.org/ver10/events/wsdl" xmlns:tns1="http://www.onvif.org/ver10/topics" xmlns:tnshik="http://www.hikvision.com/2011/event/topics" xmlns:tt="http://www.onvif.org/ver10/schema" xmlns:wsnt="http://docs.oasis-open.org/wsn/b-2" xmlns:wstop="http://docs.oasis-open.org/wsn/t-1">
  <tev:TopicNamespaceLocation>http://www.onvif.org/onvif/ver10/topics/topicns.xml</tev:TopicNamespaceLocation>
  <wsnt:FixedTopicSet>true</wsnt:FixedTopicSet>
  <wstop:TopicSet>
    <tns1:RuleEngine>
      <CellMotionDetector>
        <Motion wstop:topic="true">
          <tt:MessageDescription IsProperty="true">
            <tt:Source>
              <tt:SimpleItemDescription Name="VideoSourceConfigurationToken" Type="tt:ReferenceToken" />
              <tt:SimpleItemDescription Name="VideoAnalyticsConfigurationToken" Type="tt:ReferenceToken" />
              <tt:SimpleItemDescription Name="Rule" Type="xs:string" />
            </tt:Source>
            <tt:Data>
              <tt:SimpleItemDescription Name="IsMotion" Type="xs:boolean" />
            </tt:Data>
          </tt:MessageDescription>
        </Motion>
      </CellMotionDetector>
    </tns1:RuleEngine>
    <tns1:VideoSource>
      <MotionAlarm wstop:topic="true">
        <tt:MessageDescription IsProperty="true">
          <tt:Source>
            <tt:SimpleItemDescription Name="Source" Type="tt:ReferenceToken" />
          </tt:Source>
          <tt:Data>
            <tt:SimpleItemDescription Name="State" Type="xs:boolean" />
          </tt:Data>
        </tt:MessageDescription>
      </MotionAlarm>
    </tns1:VideoSource>
    <tns1:Device>
      <Trigger>
        <tnshik:AlarmIn wstop:topic="true">
          <tt:MessageDescription IsProperty="true">
            <tt:Source>
              <tt:SimpleItemDescription Name="AlarmInToken" Type="tt:ReferenceToken" />
            </tt:Source>
            <tt:Data>
              <tt:SimpleItemDescription Name="State" Type="xs:boolean" />
            </tt:Data>
          </tt:MessageDescription>
        </tnshik:AlarmIn>
        <DigitalInput wstop:topic="true">
          <tt:MessageDescription IsProperty="true">
            <tt:Source>
              <tt:SimpleItemDescription Name="InputToken" Type="tt:ReferenceToken" />
            </tt:Source>
            <tt:Data>
              <tt:SimpleItemDescription Name="LogicalState" Type="xs:boolean" />
            </tt:Data>
          </tt:MessageDescription>
        </DigitalInput>
        <Relay wstop:topic="true">
          <tt:MessageDescription IsProperty="true">
            <tt:Source>
              <tt:SimpleItemDescription Name="RelayToken" Type="tt:ReferenceToken" />
            </tt:Source>
            <tt:Data>
              <tt:SimpleItemDescription Name="LogicalState" Type="tt:RelayLogicalState" />
            </tt:Data>
          </tt:MessageDescription>
        </Relay>
      </Trigger>
    </tns1:Device>
    <tns1:UserAlarm>
      <tns1:IVA>
        <tns1:HumanShapeDetect wstop:topic="true">
          <tt:MessageDescription IsProperty="false">
            <tt:Source>
              <tt:SimpleItemDescription Name="VideoSourceConfigurationToken" Type="tt:ReferenceToken" />
            </tt:Source>
            <tt:Data>
              <tt:SimpleItemDescription Name="State" Type="xsd:boolean" />
            </tt:Data>
          </tt:MessageDescription>
        </tns1:HumanShapeDetect>
      </tns1:IVA>
    </tns1:UserAlarm>
  </wstop:TopicSet>
  <wsnt:TopicExpressionDialect>http://www.onvif.org/ver10/tev/topicExpression/ConcreteSet</wsnt:TopicExpressionDialect>
  <wsnt:TopicExpressionDialect>http://docs.oasis-open.org/wsn/t-1/TopicExpression/Concrete</wsnt:TopicExpressionDialect>
  <tev:MessageContentFilterDialect>http://www.onvif.org/ver10/tev/messageContentFilter/ItemFilter</tev:MessageContentFilterDialect>
  <tev:MessageContentSchemaLocation>http://www.onvif.org/ver10/schema/onvif.xsd</tev:MessageContentSchemaLocation>
</tev:GetEventPropertiesResponse>
```

</details>

### Analytics

<details><summary><code>GetSupportedRules (VideoAnalyticsToken)</code></summary>

```xml
<tan:GetSupportedRulesResponse xmlns:tan="http://www.onvif.org/ver20/analytics/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
  <tan:SupportedRules>
    <tt:RuleDescription Name="tt:CellMotionDetector">
      <tt:Parameters>
        <tt:SimpleItemDescription Type="xsd:integer" Name="MinCount" />
        <tt:SimpleItemDescription Type="xsd:integer" Name="AlarmOnDelay" />
        <tt:SimpleItemDescription Type="xsd:integer" Name="AlarmOffDelay" />
        <tt:SimpleItemDescription Type="xsd:base64Binary" Name="ActiveCells" />
      </tt:Parameters>
      <tt:Messages IsProperty="true">
        <tt:Source>
          <tt:SimpleItemDescription Type="tt:ReferenceToken" Name="VideoSourceConfigurationToken" />
          <tt:SimpleItemDescription Type="tt:ReferenceToken" Name="VideoAnalyticsConfigurationToken" />
          <tt:SimpleItemDescription Type="xsd:string" Name="Rule" />
        </tt:Source>
        <tt:Data>
          <tt:SimpleItemDescription Type="xsd:boolean" Name="IsMotion" />
        </tt:Data>
        <tt:ParentTopic>tns1:RuleEngine/CellMotionDetector/Motion</tt:ParentTopic>
      </tt:Messages>
    </tt:RuleDescription>
    <tt:RuleDescription Name="tt:SmartMotionDetector">
      <tt:Parameters>
        <tt:SimpleItemDescription Type="xsd:boolean" Name="Enable" />
        <tt:SimpleItemDescription Type="xsd:int" Name="Sensitivity" />
        <tt:SimpleItemDescription Type="xsd:duration" Name="Threshold" />
        <tt:SimpleItemDescription Type="xsd:int" Name="Type" />
        <tt:SimpleItemDescription Type="tt:Polygon" Name="Field" />
      </tt:Parameters>
      <tt:Messages IsProperty="true">
        <tt:Source>
          <tt:SimpleItemDescription Type="tt:ReferenceToken" Name="VideoSourceConfigurationToken" />
          <tt:SimpleItemDescription Type="tt:ReferenceToken" Name="VideoAnalyticsConfigurationToken" />
          <tt:SimpleItemDescription Type="xsd:string" Name="Rule" />
        </tt:Source>
        <tt:Key>
          <tt:SimpleItemDescription Type="xsd:integer" Name="ObjectId" />
        </tt:Key>
        <tt:Data>
          <tt:SimpleItemDescription Type="xsd:boolean" Name="IsInside" />
        </tt:Data>
        <tt:ParentTopic>tns1:RuleEngine/FieldDetector/ObjectsInside</tt:ParentTopic>
      </tt:Messages>
    </tt:RuleDescription>
    <tt:RuleDescription Name="tt:LineDetector">
      <tt:Parameters>
        <tt:SimpleItemDescription Type="xsd:boolean" Name="LineEnable" />
        <tt:SimpleItemDescription Type="xsd:int" Name="Sensitivity" />
        <tt:SimpleItemDescription Type="xsd:int" Name="TriggerDirec" />
        <tt:SimpleItemDescription Type="xsd:Direction" Name="Direction" />
        <tt:SimpleItemDescription Type="tt:Polygon" Name="Segments" />
      </tt:Parameters>
      <tt:Messages IsProperty="true">
        <tt:Source>
          <tt:SimpleItemDescription Type="tt:ReferenceToken" Name="VideoSourceConfigurationToken" />
          <tt:SimpleItemDescription Type="tt:ReferenceToken" Name="VideoAnalyticsConfigurationToken" />
          <tt:SimpleItemDescription Type="xsd:string" Name="Rule" />
        </tt:Source>
        <tt:Data>
          <tt:SimpleItemDescription Type="xsd:integer" Name="ObjectId" />
        </tt:Data>
        <tt:ParentTopic>tns1:RuleEngine/LineDetector/Crossed</tt:ParentTopic>
      </tt:Messages>
    </tt:RuleDescription>
  </tan:SupportedRules>
</tan:GetSupportedRulesResponse>
```

</details>

<details><summary><code>GetRules (VideoAnalyticsToken)</code></summary>

```xml
<tan:GetRulesResponse xmlns:tan="http://www.onvif.org/ver20/analytics/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
  <tan:Rule Type="tt:CellMotionDetector" Name="MyMotionDetectorRule">
    <tt:Parameters>
      <tt:SimpleItem Name="MinCount" Value="5" />
      <tt:SimpleItem Name="AlarmOnDelay" Value="100" />
      <tt:SimpleItem Name="AlarmOffDelay" Value="100" />
      <tt:SimpleItem Name="ActiveCells" Value="5wACQAAB7AA=" />
      <tt:ElementItem Name="hb_ext">
        <tt:SimpleItem Name="MotionDetectorEnable" Value="true" />
        <tt:ElementItem Name="Week" Value="Everyday">
          <tt:SimpleItem Name="time_seg" Value="00:00:00-23:59:00" />
        </tt:ElementItem>
      </tt:ElementItem>
    </tt:Parameters>
  </tan:Rule>
  <tan:Rule Type="tt:SmartMotionDetector" Name="MySmartMotionDetector">
    <tt:Parameters>
      <tt:SimpleItem Name="Enable" Value="true" />
      <tt:SimpleItem Name="Sensitivity" Value="125" />
      <tt:SimpleItem Name="Reserved" Value="0" />
      <tt:SimpleItem Name="Type" Value="1" />
      <tt:ElementItem Name="Field">
        <tt:PolygonConfiguration>
          <tt:Polygon>
            <tt:Point x="0" y="0" />
            <tt:Point x="10000" y="0" />
            <tt:Point x="10000" y="10000" />
            <tt:Point x="0" y="10000" />
          </tt:Polygon>
        </tt:PolygonConfiguration>
      </tt:ElementItem>
      <tt:ElementItem Name="Date">
        <tt:ElementItem Name="Week" Value="Everyday">
          <tt:SimpleItem Name="time_seg" Value="00:00:00-23:59:00" />
        </tt:ElementItem>
      </tt:ElementItem>
    </tt:Parameters>
  </tan:Rule>
</tan:GetRulesResponse>
```

</details>

<details><summary><code>GetSupportedAnalyticsModules (VideoAnalyticsToken)</code></summary>

```xml
<tan:GetSupportedAnalyticsModulesResponse xmlns:tan="http://www.onvif.org/ver20/analytics/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
  <tan:SupportedAnalyticsModules>
    <tt:AnalyticsModuleDescription Name="tt:CellMotionEngine">
      <tt:Parameters>
        <tt:SimpleItemDescription Type="xsd:integer" Name="Sensitivity" />
        <tt:ElementItemDescription Type="tt:CellLayout" Name="Layout" />
      </tt:Parameters>
    </tt:AnalyticsModuleDescription>
    <tt:AnalyticsModuleDescription Name="tt:CellMotionDetector">
      <tt:Parameters>
        <tt:SimpleItemDescription Type="xsd:integer" Name="MinCount" />
        <tt:SimpleItemDescription Type="xsd:integer" Name="AlarmOnDelay" />
        <tt:SimpleItemDescription Type="xsd:integer" Name="AlarmOffDelay" />
        <tt:SimpleItemDescription Type="xsd:base64Binary" Name="ActiveCells" />
      </tt:Parameters>
    </tt:AnalyticsModuleDescription>
    <tt:AnalyticsModuleDescription Name="tt:SmartMotionDetectorEngine">
      <tt:Parameters>
        <tt:SimpleItemDescription Type="xsd:boolean" Name="Enable" />
        <tt:SimpleItemDescription Type="tt:Transformation" Name="Layout" />
        <tt:SimpleItemDescription Type="tt:PolygonConfiguration" Name="Field" />
        <tt:SimpleItemDescription Type="tt:Week" Name="Date" />
        <tt:SimpleItemDescription Type="xsd:integer" Name="Type" />
      </tt:Parameters>
    </tt:AnalyticsModuleDescription>
    <tt:AnalyticsModuleDescription Name="tt:LineDetectorEngine">
      <tt:Parameters>
        <tt:SimpleItemDescription Type="xsd:boolean" Name="Enable" />
        <tt:SimpleItemDescription Type="tt:Transformation" Name="Layout" />
        <tt:SimpleItemDescription Type="tt:PolygonConfiguration" Name="Field" />
      </tt:Parameters>
      <tt:Messages IsProperty="true">
        <tt:Source>
          <tt:SimpleItemDescription Type="tt:ReferenceToken" Name="VideoSourceConfigurationToken" />
          <tt:SimpleItemDescription Type="tt:ReferenceToken" Name="VideoAnalyticsConfigurationToken" />
          <tt:SimpleItemDescription Type="xsd:string" Name="Rule" />
        </tt:Source>
        <tt:Data>
          <tt:SimpleItemDescription Type="xsd:integer" Name="ObjectId" />
        </tt:Data>
        <tt:ParentTopic>tns1:RuleEngine/LineDetector/Crossed</tt:ParentTopic>
      </tt:Messages>
    </tt:AnalyticsModuleDescription>
  </tan:SupportedAnalyticsModules>
</tan:GetSupportedAnalyticsModulesResponse>
```

</details>

<details><summary><code>GetAnalyticsModules (VideoAnalyticsToken)</code></summary>

```xml
<tan:GetAnalyticsModulesResponse xmlns:tan="http://www.onvif.org/ver20/analytics/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
  <tan:AnalyticsModule Type="tt:CellMotionEngine" Name="MyCellMotionModule">
    <tt:Parameters>
      <tt:SimpleItem Name="Sensitivity" Value="80" />
      <tt:ElementItem Name="Layout">
        <tt:CellLayout Rows="18" Columns="22">
          <tt:Transformation>
            <tt:Translate y="-1" x="-1" />
            <tt:Scale y="9.99999997E-07" x="9.99999997E-07" />
          </tt:Transformation>
        </tt:CellLayout>
      </tt:ElementItem>
    </tt:Parameters>
  </tan:AnalyticsModule>
  <tan:AnalyticsModule Type="tt:CellMotionDetector" Name="MyMotionDetectorRule">
    <tt:Parameters>
      <tt:SimpleItem Name="MinCount" Value="5" />
      <tt:SimpleItem Name="AlarmOnDelay" Value="100" />
      <tt:SimpleItem Name="AlarmOffDelay" Value="100" />
      <tt:SimpleItem Name="ActiveCells" Value="5wACQAAB7AA=" />
    </tt:Parameters>
  </tan:AnalyticsModule>
  <tan:AnalyticsModule Type="tt:SmartMotionDetectorEngine" Name="MySmartMotionDetector">
    <tt:Parameters>
      <tt:SimpleItem Name="Enable" Value="true" />
      <tt:SimpleItem Name="Type" Value="1" />
    </tt:Parameters>
  </tan:AnalyticsModule>
</tan:GetAnalyticsModulesResponse>
```

</details>

### PTZ

<details><summary><code>GetNodes</code></summary>

```xml
<tptz:GetNodesResponse xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
  <tptz:PTZNode token="ptz0">
    <tt:Name>ptz0</tt:Name>
    <tt:SupportedPTZSpaces>
      <tt:AbsolutePanTiltPositionSpace>
        <tt:URI>http://www.onvif.org/ver10/tptz/PanTiltSpaces/PositionGenericSpace</tt:URI>
        <tt:XRange>
          <tt:Min>-1</tt:Min>
          <tt:Max>1</tt:Max>
        </tt:XRange>
        <tt:YRange>
          <tt:Min>-1</tt:Min>
          <tt:Max>1</tt:Max>
        </tt:YRange>
      </tt:AbsolutePanTiltPositionSpace>
      <tt:AbsoluteZoomPositionSpace>
        <tt:URI>http://www.onvif.org/ver10/tptz/ZoomSpaces/PositionGenericSpace</tt:URI>
        <tt:XRange>
          <tt:Min>-1</tt:Min>
          <tt:Max>1</tt:Max>
        </tt:XRange>
      </tt:AbsoluteZoomPositionSpace>
      <tt:RelativePanTiltTranslationSpace>
        <tt:URI>http://www.onvif.org/ver10/tptz/PanTiltSpaces/TranslationGenericSpace</tt:URI>
        <tt:XRange>
          <tt:Min>-1</tt:Min>
          <tt:Max>1</tt:Max>
        </tt:XRange>
        <tt:YRange>
          <tt:Min>-1</tt:Min>
          <tt:Max>1</tt:Max>
        </tt:YRange>
      </tt:RelativePanTiltTranslationSpace>
      <tt:RelativeZoomTranslationSpace>
        <tt:URI>http://www.onvif.org/ver10/tptz/ZoomSpaces/TranslationGenericSpace</tt:URI>
        <tt:XRange>
          <tt:Min>-1</tt:Min>
          <tt:Max>1</tt:Max>
        </tt:XRange>
      </tt:RelativeZoomTranslationSpace>
      <tt:ContinuousPanTiltVelocitySpace>
        <tt:URI>http://www.onvif.org/ver10/tptz/PanTiltSpaces/VelocityGenericSpace</tt:URI>
        <tt:XRange>
          <tt:Min>-1</tt:Min>
          <tt:Max>1</tt:Max>
        </tt:XRange>
        <tt:YRange>
          <tt:Min>-1</tt:Min>
          <tt:Max>1</tt:Max>
        </tt:YRange>
      </tt:ContinuousPanTiltVelocitySpace>
      <tt:ContinuousZoomVelocitySpace>
        <tt:URI>http://www.onvif.org/ver10/tptz/ZoomSpaces/VelocityGenericSpace</tt:URI>
        <tt:XRange>
          <tt:Min>-1</tt:Min>
          <tt:Max>1</tt:Max>
        </tt:XRange>
      </tt:ContinuousZoomVelocitySpace>
      <tt:PanTiltSpeedSpace>
        <tt:URI>http://www.onvif.org/ver10/tptz/PanTiltSpaces/GenericSpeedSpace</tt:URI>
        <tt:XRange>
          <tt:Min>-1</tt:Min>
          <tt:Max>1</tt:Max>
        </tt:XRange>
      </tt:PanTiltSpeedSpace>
      <tt:ZoomSpeedSpace>
        <tt:URI>http://www.onvif.org/ver10/tptz/ZoomSpaces/ZoomGenericSpeedSpace</tt:URI>
        <tt:XRange>
          <tt:Min>-1</tt:Min>
          <tt:Max>1</tt:Max>
        </tt:XRange>
      </tt:ZoomSpeedSpace>
    </tt:SupportedPTZSpaces>
    <tt:MaximumNumberOfPresets>255</tt:MaximumNumberOfPresets>
    <tt:HomeSupported>true</tt:HomeSupported>
    <tt:AuxiliaryCommands>tt:Wiper|On</tt:AuxiliaryCommands>
    <tt:AuxiliaryCommands>tt:Wiper|Off</tt:AuxiliaryCommands>
    <tt:AuxiliaryCommands>tt:Lamp|On</tt:AuxiliaryCommands>
    <tt:AuxiliaryCommands>tt:Lamp|Off</tt:AuxiliaryCommands>
  </tptz:PTZNode>
</tptz:GetNodesResponse>
```

</details>

<details><summary><code>GetConfigurations</code></summary>

```xml
<tptz:GetConfigurationsResponse xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
  <tptz:PTZConfiguration token="ptz0">
    <tt:Name>ptz0</tt:Name>
    <tt:UseCount>2</tt:UseCount>
    <tt:NodeToken>ptz0</tt:NodeToken>
    <tt:DefaultAbsolutePantTiltPositionSpace>http://www.onvif.org/ver10/tptz/PanTiltSpaces/PositionGenericSpace</tt:DefaultAbsolutePantTiltPositionSpace>
    <tt:DefaultAbsoluteZoomPositionSpace>http://www.onvif.org/ver10/tptz/ZoomSpaces/PositionGenericSpace</tt:DefaultAbsoluteZoomPositionSpace>
    <tt:DefaultRelativePanTiltTranslationSpace>http://www.onvif.org/ver10/tptz/PanTiltSpaces/TranslationGenericSpace</tt:DefaultRelativePanTiltTranslationSpace>
    <tt:DefaultRelativeZoomTranslationSpace>http://www.onvif.org/ver10/tptz/ZoomSpaces/TranslationGenericSpace</tt:DefaultRelativeZoomTranslationSpace>
    <tt:DefaultContinuousPanTiltVelocitySpace>http://www.onvif.org/ver10/tptz/PanTiltSpaces/VelocityGenericSpace</tt:DefaultContinuousPanTiltVelocitySpace>
    <tt:DefaultContinuousZoomVelocitySpace>http://www.onvif.org/ver10/tptz/ZoomSpaces/VelocityGenericSpace</tt:DefaultContinuousZoomVelocitySpace>
    <tt:DefaultPTZSpeed>
      <tt:PanTilt x="1" y="1" space="http://www.onvif.org/ver10/tptz/PanTiltSpaces/GenericSpeedSpace" />
      <tt:Zoom x="1" space="http://www.onvif.org/ver10/tptz/ZoomSpaces/ZoomGenericSpeedSpace" />
    </tt:DefaultPTZSpeed>
    <tt:DefaultPTZTimeout>PT00H01M00S</tt:DefaultPTZTimeout>
    <tt:PanTiltLimits>
      <tt:Range>
        <tt:URI>http://www.onvif.org/ver10/tptz/PanTiltSpaces/PositionGenericSpace</tt:URI>
        <tt:XRange>
          <tt:Min>-1</tt:Min>
          <tt:Max>1</tt:Max>
        </tt:XRange>
        <tt:YRange>
          <tt:Min>-1</tt:Min>
          <tt:Max>1</tt:Max>
        </tt:YRange>
      </tt:Range>
    </tt:PanTiltLimits>
    <tt:ZoomLimits>
      <tt:Range>
        <tt:URI>http://www.onvif.org/ver10/tptz/ZoomSpaces/PositionGenericSpace</tt:URI>
        <tt:XRange>
          <tt:Min>-1</tt:Min>
          <tt:Max>1</tt:Max>
        </tt:XRange>
      </tt:Range>
    </tt:ZoomLimits>
  </tptz:PTZConfiguration>
</tptz:GetConfigurationsResponse>
```

</details>

### DeviceIO

<details><summary><code>GetServiceCapabilities</code></summary>

```xml
<tmd:GetServiceCapabilitiesResponse xmlns:tmd="http://www.onvif.org/ver10/deviceIO/wsdl">
  <tmd:Capabilities VideoSources="1" VideoOutputs="0" AudioSources="1" AudioOutputs="1" RelayOutputs="4" SerialPorts="0" DigitalInputs="1" DigitalInputOptions="true" />
</tmd:GetServiceCapabilitiesResponse>
```

</details>

<details><summary><code>GetVideoSources</code></summary>

```xml
<tmd:GetVideoSourcesResponse xmlns:tmd="http://www.onvif.org/ver10/deviceIO/wsdl">
  <tmd:Token>VideoSourceMain</tmd:Token>
</tmd:GetVideoSourcesResponse>
```

</details>

<details><summary><code>GetAudioSources</code></summary>

```xml
<tmd:GetAudioSourcesResponse xmlns:tmd="http://www.onvif.org/ver10/deviceIO/wsdl">
  <tmd:Token>AudioMainToken</tmd:Token>
</tmd:GetAudioSourcesResponse>
```

</details>

<details><summary><code>GetAudioOutputs</code></summary>

```xml
<tmd:GetAudioOutputsResponse xmlns:tmd="http://www.onvif.org/ver10/deviceIO/wsdl" />
```

</details>

<details><summary><code>GetDigitalInputs</code></summary>

```xml
<tmd:GetDigitalInputsResponse xmlns:tmd="http://www.onvif.org/ver10/deviceIO/wsdl">
  <tmd:DigitalInputs token="Input1" IdleState="open" />
</tmd:GetDigitalInputsResponse>
```

</details>
