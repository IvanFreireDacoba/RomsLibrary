# Imagen de build reproducible para RomLibraryClient. El SDK de Android y
# Gradle NO se empaquetan aquí (ya existen provisionados en el host, en
# ~/android-sdk y ~/gradle-dist) -- se montan como volúmenes de solo lectura
# al ejecutar el contenedor (ver build.sh), así el build es reproducible sin
# redescargar cientos de MB en cada imagen.
FROM eclipse-temurin:21-jdk-jammy

ENV ANDROID_HOME=/opt/android-sdk
ENV PATH="/opt/gradle/bin:${PATH}"

WORKDIR /workspace
