# Bootstrap — Instalación Manual de APKs

El setup inicial del teléfono requiere instalación manual de 4 APKs. Esto se hace una sola vez.

## APKs necesarios

| APK | Fuente | Para qué |
|-----|--------|---------|
| Termux | F-Droid | Terminal Linux en Android |
| TermuxBoot | F-Droid | Ejecuta scripts en cada boot |
| TermuxAPI | F-Droid | Acceso a microphone desde Termux |
| FullyKiosk Browser | fullykiosk.com | Browser kiosk para la pantalla |

## Pasos

### 1. Instalar F-Droid

1. Abrir navegador en el teléfono
2. Ir a https://f-droid.org
3. Descargar e instalar F-Droid

### 2. Instalar Termux y componentes

1. Abrir F-Droid
2. Buscar "Termux" e instalar
3. Abrir Termux - aceptar la inicialización
4. En Termux, ejecutar:
   ```
   pkg update && pkg install openssh -y
   passwd
   ```
   - Poner una contraseña (será la PHONE_PASS)
5. Ejecutar `sshd` para arrancar el servidor SSH

### 3. Instalar TermuxBoot

1. En F-Droid, buscar "TermuxBoot" e instalar
2. No requiere configuración adicional

### 4. Instalar TermuxAPI

1. En F-Droid, buscar "TermuxAPI" e instalar
2. No requiere configuración adicional

### 5. Instalar FullyKiosk Browser

1. Ir a https://www.fully-kiosk.com y descargar FullyKiosk Browser
2. Instalar el APK descargado
3. Configurar según [docs/fullyKiosk-setup.md](fullyKiosk-setup.md)

## Verificación

Después de instalar Termux:

```bash
# Desde el Mac, verificar SSH
ssh <IP_DEL_TELEFONO> -p 8022 "echo 'SSH OK'"
```

## Siguiente paso

Una vez instaladas las 4 APKs y configurado SSH, ejecutar:

```bash
./jota-voice init
./jota-voice setup
```
