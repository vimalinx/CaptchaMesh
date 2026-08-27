plugins {
    id("com.android.application")
}

android {
    namespace = "app.captchamesh"
    compileSdk = 35

    defaultConfig {
        applicationId = "app.captchamesh"
        minSdk = 29
        targetSdk = 35
        versionCode = 23
        versionName = "0.19.1"
    }

    signingConfigs {
        create("release") {
            val keystorePath = System.getenv("ANDROID_KEYSTORE_PATH")
            if (!keystorePath.isNullOrBlank()) {
                storeFile = file(keystorePath)
                storePassword = System.getenv("ANDROID_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("ANDROID_KEY_ALIAS")
                keyPassword = System.getenv("ANDROID_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("release")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

dependencies {
    implementation("androidx.appcompat:appcompat:1.8.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("com.google.android.material:material:1.14.0")
    implementation("androidx.webkit:webkit:1.17.0")
}
