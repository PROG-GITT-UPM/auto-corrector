# Ejercicio 3 — Clave WiFi segura
nombre_red: str = input("Nombre de la red (SSID): ")
clave: str = input("Clave WiFi: ")

# Cada requisito es un bool independiente
longitud_ok: bool = len(clave) >= 12
tiene_mayuscula: bool = clave != clave.lower()
tiene_minuscula: bool = clave != clave.upper()
sin_espacios: bool = " " not in clave
tiene_simbolos: bool = not clave.isalnum()
no_contiene_red: bool = nombre_red.lower() not in clave.lower()

# Puntuación: True vale 1 y False vale 0 al convertir a int
puntuacion: int = (int(longitud_ok) + int(tiene_mayuscula) + int(tiene_minuscula)
                   + int(sin_espacios) + int(tiene_simbolos) + int(no_contiene_red))
segura: bool = puntuacion == 6

print()
print(f"--- Análisis de la clave de «{nombre_red}» ---")
print(f"Al menos 12 caracteres:        {longitud_ok}")
print(f"Tiene mayúsculas:              {tiene_mayuscula}")
print(f"Tiene minúsculas:              {tiene_minuscula}")
print(f"Sin espacios:                  {sin_espacios}")
print(f"Tiene símbolos:                {tiene_simbolos}")
print(f"No contiene el nombre de red:  {no_contiene_red}")
print(f"Puntuación:                    {puntuacion}/6")
print(f"Clave segura:                  {segura}")
