# Ejercicio 1 — Validador de número de teléfono
telefono: str = input("Número de teléfono: ")

# Normalizar: quitar espacios y el prefijo internacional de España
sin_espacios: str = telefono.replace(" ", "")
tiene_prefijo: bool = sin_espacios.startswith("+34")
numero: str = sin_espacios.replace("+34", "")

# Condiciones independientes (cada una es un bool)
longitud_ok: bool = len(numero) == 9
solo_cifras: bool = numero.isdigit()
primera_cifra: str = numero[0]
primera_cifra_ok: bool = primera_cifra in "6789"
valido: bool = longitud_ok and solo_cifras and primera_cifra_ok

# Tipo de línea
es_movil: bool = primera_cifra in "67"
es_fijo: bool = primera_cifra in "89"

# Formato "612 345 678" con tres trozos
formateado: str = f"{numero[0:3]} {numero[3:6]} {numero[6:9]}"

print()
print("--- Análisis del número ---")
print(f"Prefijo +34:          {tiene_prefijo}")
print(f"Número sin prefijo:   {numero}")
print(f"Tiene 9 cifras:       {longitud_ok}")
print(f"Solo cifras:          {solo_cifras}")
print(f"Empieza por 6/7/8/9:  {primera_cifra_ok}")
print(f"Válido:               {valido}")
print(f"Es móvil:             {es_movil}")
print(f"Es fijo:              {es_fijo}")
print(f"Formateado:           {formateado}")
