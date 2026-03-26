import CoreWLAN
import Foundation

guard let iface = CWWiFiClient.shared().interface() else {
    print("STATUS:off")
    exit(0)
}

let powered = iface.powerOn()
let rssi    = iface.rssiValue()
let noise   = iface.noiseMeasurement()
let txRate  = iface.transmitRate()

var channelNum = 0
var band       = ""
var width      = ""

if let ch = iface.wlanChannel() {
    channelNum = ch.channelNumber
    switch ch.channelBand {
    case .band2GHz: band = "2.4GHz"
    case .band5GHz: band = "5GHz"
    case .band6GHz: band = "6GHz"
    default: band = "unknown"
    }
    switch ch.channelWidth {
    case .width20MHz:  width = "20MHz"
    case .width40MHz:  width = "40MHz"
    case .width80MHz:  width = "80MHz"
    case .width160MHz: width = "160MHz"
    default: width = ""
    }
}

print("STATUS:\(powered ? "connected" : "off")")
print("RSSI:\(rssi)")
print("NOISE:\(noise)")
print("TXRATE:\(Int(txRate))")
print("CHANNEL:\(channelNum)")
print("BAND:\(band)")
print("WIDTH:\(width)")
