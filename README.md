# XAUUSD Semanal

Reporte semanal del oro que se genera solo, gratis, en GitHub Actions. No usa IA ni claves de API.

- **Domingo 17:00 (Ecuador):** calcula el sesgo, los niveles, los escenarios, el calendario de la semana y el repaso de la semana anterior.
- **Lunes a viernes (07:00 y 16:30):** actualiza el precio, el rango de la semana, los niveles tocados y el calendario.

## Cómo funciona
- **Sesgo:** suma 6 factores (precio frente a las medias de 20 y 50 días, semana anterior del oro, DXY, bono a 10 años y Brent). Cada factor vale +1, 0 o −1.
- **Niveles:** confluencia de máximos y mínimos de semanas anteriores, swings diarios, máximo y mínimo del mes y números redondos. Se marcan como alcanzables si caen dentro del rango semanal medio.
- **Calendario:** feed público de Forex Factory, con horas en Ecuador y una lectura para el oro (datos fuertes = negativo, débiles = favorable).

## Archivos
- `index.html`: el dashboard.
- `build.py`: genera `data.json`.
- `history/`: reportes de semanas anteriores.

Para rehacer el reporte a mano, ve a **Actions → Reporte XAUUSD → Run workflow**.

Material educativo, no es asesoramiento financiero.
