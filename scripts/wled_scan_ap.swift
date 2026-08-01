// wled_scan_ap.swift — list visible Wi-Fi networks as JSON, for wled_apjoin.py.
//
// Why Swift: on macOS 26 the `airport` tool is gone, and `system_profiler` / `ipconfig getsummary`
// redact SSID and BSSID strings. CoreWLAN is the only interface that still returns names.
//
// Run interpreted (`swift wled_scan_ap.swift`) — no build step, no binary to keep in sync.
//
// Usage:
//   swift wled_scan_ap.swift            # broadcast sweep
//   swift wled_scan_ap.swift WLED-AP    # targeted probe for one SSID (more reliable — a sweep
//                                       # has been observed to miss an AP that was plainly there)
//
// Output: {"ok":true,"networks":[{"ssid":…,"rssi":…,"channel":…,"band":…,"open":…,"bssid":…}]}
// `bssid` is usually null: macOS withholds it without Location Services authorisation.

import CoreWLAN
import Foundation

func emit(_ obj: [String: Any]) -> Never {
    let data = try! JSONSerialization.data(withJSONObject: obj, options: [])
    print(String(data: data, encoding: .utf8)!)
    exit(obj["ok"] as? Bool == true ? 0 : 1)
}

guard let iface = CWWiFiClient.shared().interface() else {
    emit(["ok": false, "error": "no Wi-Fi interface"])
}

let target = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : nil

do {
    let nets = try iface.scanForNetworks(withSSID: target?.data(using: .utf8))
    var out: [[String: Any]] = []
    for n in nets {
        // A network whose SSID is nil means macOS is withholding names — report it rather than
        // silently emitting an empty list, which reads as "no devices found".
        guard let ssid = n.ssid else { continue }
        let ch = n.wlanChannel?.channelNumber ?? -1
        // Use CoreWLAN's own band rather than inferring from the channel number: 6GHz channels are
        // numbered 1-233, so a `ch <= 14` test misclassifies them as 2.4GHz.
        var band = -1
        switch n.wlanChannel?.channelBand {
        case .some(.band2GHz): band = 2
        case .some(.band5GHz): band = 5
        default: band = ch > 0 && ch <= 14 ? 2 : -1
        }
        out.append([
            "ssid": ssid,
            "rssi": n.rssiValue,
            "channel": ch,
            "band": band,
            // ESP32 APs are frequently open; treat "no security" as a candidate signal.
            "open": n.supportsSecurity(.none),
            "bssid": n.bssid ?? NSNull(),
        ])
    }
    let withheld = nets.count > 0 && out.isEmpty
    emit([
        "ok": true,
        "networks": out,
        "scanned": nets.count,
        // Distinguish "nothing in range" from "macOS is redacting names". Without this the caller
        // cannot tell a genuinely empty area from a missing Location Services grant.
        "ssids_withheld": withheld,
    ])
} catch {
    emit(["ok": false, "error": "\(error)"])
}
