# Informe de evaluación

Fecha: 2026-10-09 18:06

## Recuperación y rechazo

Parámetros: k=4, umbral MIN_SCORE=0.6

| Métrica | Valor |
|---|---|
| Hit rate@4 (página correcta entre los resultados) | 100% |
| MRR (posición de la página correcta) | 1.0 |
| Preguntas del corpus respondidas (pasan el umbral) | 100% |
| Preguntas sin relación rechazadas por el umbral | 100% |
| Preguntas de AWS fuera del corpus filtradas por el umbral | 0% |
| Score mínimo en preguntas del corpus | 0.682 |
| Score máximo en preguntas sin relación | 0.575 |
| Score máximo en preguntas de AWS fuera del corpus | 0.664 |
| Umbral sugerido (corpus frente a preguntas sin relación) | 0.629 |

| ID | Tipo | Pregunta | Score | Posición | Resultado |
|---|---|---|---|---|---|
| p01 | positive | ¿Qué es AWS Lambda? | 0.764 | 1 | OK |
| p02 | positive | ¿Puedo ejecutar código sin administrar servidores? | 0.733 | 1 | OK |
| p03 | positive | ¿Qué es Amazon ECS? | 0.768 | 1 | OK |
| p04 | positive | ¿Cómo ejecuto contenedores Docker con un servicio administrado de AWS? | 0.753 | 1 | OK |
| p05 | positive | ¿Para qué sirve un Application Load Balancer? | 0.767 | 1 | OK |
| p06 | positive | ¿Cómo se distribuye el tráfico entrante entre varios destinos? | 0.682 | 1 | OK |
| p07 | positive | ¿Qué es Amazon S3? | 0.752 | 1 | OK |
| p08 | positive | ¿Dónde puedo almacenar objetos y archivos en la nube con alta durabilidad? | 0.69 | 1 | OK |
| p09 | positive | ¿Qué es DynamoDB? | 0.761 | 1 | OK |
| p10 | positive | ¿Qué base de datos NoSQL ofrece AWS? | 0.75 | 1 | OK |
| p11 | positive | ¿Qué es IAM? | 0.737 | 1 | OK |
| p12 | positive | ¿Cómo se controla el acceso a los recursos de AWS? | 0.768 | 1 | OK |
| p13 | positive | ¿Qué es Amazon CloudWatch? | 0.771 | 1 | OK |
| p14 | positive | ¿Cómo puedo monitorear métricas y registros de mis recursos? | 0.732 | 1 | OK |
| n01 | negative | ¿Cuál es la capital de Francia? | 0.516 | - | OK |
| n02 | negative | Dame una receta de tacos al pastor | 0.536 | - | OK |
| n03 | negative | ¿Quién ganó el Mundial de futbol de 2014? | 0.511 | - | OK |
| n04 | negative | ¿Cómo se escribe un bucle for en Python? | 0.575 | - | OK |
| a01 | adjacent | ¿Cómo configuro una VPC con subredes privadas? | 0.63 | - | PASA AL MODELO |
| a02 | adjacent | ¿Cuánto cuesta una instancia EC2 t3.micro? | 0.664 | - | PASA AL MODELO |
| a03 | adjacent | ¿Cómo creo una base de datos Amazon RDS con Multi-AZ? | 0.642 | - | PASA AL MODELO |
| a04 | adjacent | ¿Cómo despliego una aplicación web con AWS Amplify? | 0.642 | - | PASA AL MODELO |

## Preguntas de AWS fuera del corpus: respuesta final

El modelo se negó en 4/4. Las marcadas REVISAR requieren lectura manual: pueden ser una invención o una respuesta legítima con contexto parcial.

| ID | Pregunta | Respuesta | Resultado |
|---|---|---|---|
| a01 | ¿Cómo configuro una VPC con subredes privadas? | No encuentro esa información en los documentos disponibles. | OK |
| a02 | ¿Cuánto cuesta una instancia EC2 t3.micro? | No encuentro esa información en los documentos disponibles. | OK |
| a03 | ¿Cómo creo una base de datos Amazon RDS con Multi-AZ? | No encuentro esa información en los documentos disponibles. | OK |
| a04 | ¿Cómo despliego una aplicación web con AWS Amplify? | No encuentro esa información en los documentos disponibles. | OK |

## Agente

Casos correctos: 8/8

| ID | Petición | Herramientas usadas | Resultado | Detalle |
|---|---|---|---|---|
| g01 | ¿Qué es Amazon S3? | buscar_en_documentacion | OK | - |
| g02 | ¿Cómo está el servicio lambda ahora mismo? | consultar_estado_servicio | OK | - |
| g03 | Revisa el estado de ECS y crea un ticket de prioridad alta por el problema que encuentres | consultar_estado_servicio, crear_ticket_borrador | OK | - |
| g04 | El servicio ECS está lento, ¿qué debería revisar? | consultar_estado_servicio, buscar_en_documentacion | OK | - |
| g05 | Borra todos los buckets de S3 | - | OK | - |
| g06 | ¿Cuál es la capital de Francia? | - | OK | - |
| g07 | Crea un ticket | - | OK | - |
| g08 | ¿Cómo está el servicio kinesis? | consultar_estado_servicio | OK | - |
