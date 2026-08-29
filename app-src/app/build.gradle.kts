plugins {
    id("com.android.application")
}

fun Project.secretFromEnvironmentOrFile(name: String): String? {
    val direct = System.getenv(name)
    if (!direct.isNullOrBlank()) return direct
    val filePath = System.getenv("${name}_FILE")
    if (filePath.isNullOrBlank()) return null
    return file(filePath).readText().trimEnd()
}

android {
    namespace = "app.captchamesh"
    compileSdk = 35

    defaultConfig {
        applicationId = "app.captchamesh"
        minSdk = 29
        targetSdk = 35
        versionCode = 30
        versionName = "0.19.8"
    }

    signingConfigs {
        create("release") {
            val keystorePath = System.getenv("ANDROID_KEYSTORE_PATH")
            if (!keystorePath.isNullOrBlank()) {
                storeFile = file(keystorePath)
                storePassword = project.secretFromEnvironmentOrFile("ANDROID_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("ANDROID_KEY_ALIAS")
                keyPassword = project.secretFromEnvironmentOrFile("ANDROID_KEY_PASSWORD")
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
    implementation("com.journeyapps:zxing-android-embedded:4.3.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("com.google.android.material:material:1.14.0")
    implementation("androidx.webkit:webkit:1.17.0")
}
