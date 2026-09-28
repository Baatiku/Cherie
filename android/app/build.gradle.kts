plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

val rootLabEnabled = providers.gradleProperty("omnilive.rootLab")
    .orNull
    ?.equals("true",ignoreCase=true)
    ?: false

val buildCommit = providers.environmentVariable("OMNILIVE_BUILD_SHA")
    .orElse(providers.environmentVariable("RENDER_GIT_COMMIT"))
    .orElse(providers.gradleProperty("omnilive.gitSha"))
    .orNull
    ?: "dev"

val defaultBackendUrl = providers.environmentVariable("OMNILIVE_DEFAULT_BACKEND_URL")
    .orNull
    ?.trim()
    ?.takeIf { it.isNotBlank() }
    ?: "http://10.0.2.2:8000"

android {
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    namespace = "com.baatiku.omnilive"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.baatiku.omnilive"
        minSdk = 26
        targetSdk = 35
        versionCode = 6
        versionName = "0.6.0"
        buildConfigField("String", "DEFAULT_BACKEND_URL", "\"${defaultBackendUrl.replace("\"", "")}\"")
        buildConfigField("boolean", "ROOT_LAB_ENABLED", rootLabEnabled.toString())
        buildConfigField("String", "BUILD_COMMIT", "\"${buildCommit.replace("\"", "")}\"")
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    sourceSets {
        if(rootLabEnabled) {
            getByName("main").java.srcDir("src/rootlab/java")
        }
    }
}

dependencies {
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
    if(rootLabEnabled) {
        implementation(project(":rootlab"))
    }
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.activity:activity-compose:1.10.0")
    implementation("androidx.compose.ui:ui:1.7.6")
    implementation("androidx.compose.material3:material3:1.3.1")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.7")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.7")
    implementation("androidx.lifecycle:lifecycle-service:2.8.7")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")
    implementation("io.github.webrtc-sdk:android:150.7871.01")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
}
