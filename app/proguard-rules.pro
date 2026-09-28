-dontwarn io.github.libxposed.annotation.**
-adaptresourcefilecontents META-INF/xposed/java_init.list
-keep public class com.baatiku.omnicam.xposed.OmniCamModule extends io.github.libxposed.api.XposedModule {
    public <init>();
}
-keep class org.webrtc.** { *; }
