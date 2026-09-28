package com.baatiku.omnilive.stream

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import android.os.Build
import android.os.PowerManager

class DeviceHealthMonitor(context:Context) {
    private val appContext=context.applicationContext
    private val powerManager=appContext.getSystemService(PowerManager::class.java)
    private var started=false

    private val batteryReceiver=object:BroadcastReceiver() {
        override fun onReceive(context:Context?,intent:Intent?) {
            if(intent==null) return
            updateBattery(intent)
        }
    }

    private val thermalListener=if(Build.VERSION.SDK_INT>=Build.VERSION_CODES.Q) {
        PowerManager.OnThermalStatusChangedListener { status ->
            LiveTelemetry.update { it.copy(thermalStatus=thermalLabel(status)) }
        }
    } else null

    fun start() {
        if(started) return
        started=true
        val filter=IntentFilter(Intent.ACTION_BATTERY_CHANGED)
        val sticky=if(Build.VERSION.SDK_INT>=Build.VERSION_CODES.TIRAMISU) {
            appContext.registerReceiver(batteryReceiver,filter,Context.RECEIVER_NOT_EXPORTED)
        } else {
            @Suppress("DEPRECATION")
            appContext.registerReceiver(batteryReceiver,filter)
        }
        sticky?.let(::updateBattery)

        if(Build.VERSION.SDK_INT>=Build.VERSION_CODES.Q && thermalListener!=null) {
            powerManager.addThermalStatusListener(thermalListener)
            LiveTelemetry.update { it.copy(thermalStatus=thermalLabel(powerManager.currentThermalStatus)) }
        } else {
            LiveTelemetry.update { it.copy(thermalStatus="unsupported") }
        }
    }

    fun stop() {
        if(!started) return
        started=false
        try { appContext.unregisterReceiver(batteryReceiver) } catch(_:Throwable) {}
        if(Build.VERSION.SDK_INT>=Build.VERSION_CODES.Q && thermalListener!=null) {
            try { powerManager.removeThermalStatusListener(thermalListener) } catch(_:Throwable) {}
        }
    }

    private fun updateBattery(intent:Intent) {
        val level=intent.getIntExtra(BatteryManager.EXTRA_LEVEL,-1)
        val scale=intent.getIntExtra(BatteryManager.EXTRA_SCALE,100)
        val percent=if(level>=0 && scale>0) ((level*100.0)/scale).toInt().coerceIn(0,100) else null
        val status=intent.getIntExtra(BatteryManager.EXTRA_STATUS,BatteryManager.BATTERY_STATUS_UNKNOWN)
        val charging=status==BatteryManager.BATTERY_STATUS_CHARGING || status==BatteryManager.BATTERY_STATUS_FULL
        val temperatureTenths=intent.getIntExtra(BatteryManager.EXTRA_TEMPERATURE,Int.MIN_VALUE)
        val temperature=if(temperatureTenths==Int.MIN_VALUE) null else temperatureTenths/10.0
        LiveTelemetry.update {
            it.copy(
                batteryPercent=percent,
                batteryCharging=charging,
                batteryTemperatureC=temperature
            )
        }
    }

    private fun thermalLabel(status:Int):String {
        if(Build.VERSION.SDK_INT<Build.VERSION_CODES.Q) return "unsupported"
        return when(status) {
            PowerManager.THERMAL_STATUS_NONE -> "none"
            PowerManager.THERMAL_STATUS_LIGHT -> "light"
            PowerManager.THERMAL_STATUS_MODERATE -> "moderate"
            PowerManager.THERMAL_STATUS_SEVERE -> "severe"
            PowerManager.THERMAL_STATUS_CRITICAL -> "critical"
            PowerManager.THERMAL_STATUS_EMERGENCY -> "emergency"
            PowerManager.THERMAL_STATUS_SHUTDOWN -> "shutdown"
            else -> "unknown"
        }
    }
}
