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
        versionCode = 21
        versionName = "0.18.3"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
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
