# Ejercicio 2 — Letra del DNI
letras_dni: str = "TRWAGMYFPDXBNJZSQVHLCKE"

dni: str = input("DNI (8 cifras y letra, p. ej. 12345678Z): ")

# Normalizar: sin espacios alrededor y letra en mayúscula
dni_limpio: str = dni.strip().upper()

# Separar la parte numérica (un trozo) y la letra (un carácter)
numero: int = int(dni_limpio[0:8])
letra_introducida: str = dni_limpio[8]

# El cálculo real: el resto de dividir entre 23 es el índice de la letra
resto: int = numero % 23
letra_correcta: str = letras_dni[resto]
es_correcto: bool = letra_introducida == letra_correcta

print()
print(f"Número:            {numero}")
print(f"Resto entre 23:    {resto}")
print(f"Letra correcta:    {letra_correcta}")
print(f"Letra introducida: {letra_introducida}")
print(f"DNI válido:        {es_correcto}")
