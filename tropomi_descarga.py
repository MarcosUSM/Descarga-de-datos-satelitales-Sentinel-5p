#!/usr/bin/env python3
"""
Pipeline de descarga de datos Sentinel-5P (TROPOMI)
Autor: Marcos Pereira Cofré
"""

import requests
import os
import xarray as xr
import numpy as np
from datetime import datetime, timedelta
from shapely import wkt
from shapely.geometry import box

# ==========================================
# 1. CONFIGURACIÓN DEL USUARIO
# ==========================================
USERNAME = 'tu_correo@ejemplo.com'  # REEMPLAZAR CON TU CORREO DE COPERNICUS
PASSWORD = 'tu_contraseña'          # REEMPLAZAR CON TU CONTRASEÑA

# ==========================================
# 2. PARÁMETROS DE BÚSQUEDA Y DOMINIO
# ==========================================
FECHA_INICIO = "2023-09-10"         # DISPONIBLE DESDE 2018-05-01 
FECHA_FIN = "2023-12-31"

# Coordenadas (BBOX): [lon_min, lat_min, lon_max, lat_max] (Chile continental)
LAT_LIMITS = [-56.5, -16.5]
LON_LIMITS = [-76.5, -65.5]

CARPETA_DESTINO = "Input"
os.makedirs(CARPETA_DESTINO, exist_ok=True)

UMBRAL_MIN_MB = 30 # Tamaño mínimo para considerar una órbita válida (variable según tamaño del BBOX)

# Variables globales para el token
_ACCESS_TOKEN = None
_TOKEN_EXPIRY_TIME = None

# ==========================================
# FUNCIONES DE API Y AUTENTICACIÓN
# ==========================================
def get_token():
    """Obtiene o reutiliza el token de acceso de la API de Copernicus."""
    global _ACCESS_TOKEN, _TOKEN_EXPIRY_TIME
    
    if _ACCESS_TOKEN and _TOKEN_EXPIRY_TIME and datetime.now() < _TOKEN_EXPIRY_TIME:
        return _ACCESS_TOKEN

    print("🔑 Autenticando en Copernicus (Generando nuevo token)...")
    auth_url = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
    data = {
        "grant_type": "password",
        "username": USERNAME,
        "password": PASSWORD,
        "client_id": "cdse-public"
    }
    r = requests.post(auth_url, data=data)
    r.raise_for_status()
        
    token_data = r.json()
    _ACCESS_TOKEN = token_data['access_token']
    expires_in = token_data.get('expires_in', 600)
    _TOKEN_EXPIRY_TIME = datetime.now() + timedelta(seconds=expires_in - 120)
    
    return _ACCESS_TOKEN

def buscar_productos_dia(dia_actual_str):
    """Busca productos L2__NO2 (OFFL o RPRO) que intersecten el BBOX para un día específico."""
    inicio_dia = f"{dia_actual_str}T00:00:00.000Z"
    fin_dia = f"{dia_actual_str}T23:59:59.000Z"
    
    polygon = f"POLYGON(({LON_LIMITS[0]} {LAT_LIMITS[0]}, {LON_LIMITS[1]} {LAT_LIMITS[0]}, {LON_LIMITS[1]} {LAT_LIMITS[1]}, {LON_LIMITS[0]} {LAT_LIMITS[1]}, {LON_LIMITS[0]} {LAT_LIMITS[0]}))"
    
    url = (f"https://catalogue.dataspace.copernicus.eu/odata/v1/Products?$filter="
           f"Collection/Name eq 'SENTINEL-5P' and contains(Name,'L2__NO2') and "
           f"(contains(Name,'OFFL') or contains(Name,'RPRO')) and "
           f"OData.CSC.Intersects(area=geography'SRID=4326;{polygon}') and "
           f"ContentDate/Start ge {inicio_dia} and ContentDate/Start le {fin_dia}")
    
    r = requests.get(url)
    r.raise_for_status()
    return r.json().get('value', [])

# ==========================================
# FUNCIONES DE GEOPROCESAMIENTO
# ==========================================
def recortar_nc(input_path, output_path):
    """Recorta espacialmente el NetCDF conservando la jerarquía de grupos."""
    try:
        ds_prod = xr.open_dataset(input_path, group='PRODUCT', engine='netcdf4')
        lats = ds_prod['latitude'].isel(time=0).values
        lons = ds_prod['longitude'].isel(time=0).values
        
        mask = (lats >= LAT_LIMITS[0]) & (lats <= LAT_LIMITS[1]) & \
               (lons >= LON_LIMITS[0]) & (lons <= LON_LIMITS[1])
        
        if not np.any(mask):
            ds_prod.close()
            return False

        rows = np.any(mask, axis=1)
        cols = np.any(mask, axis=0)
        rmin, rmax = np.where(rows)[0][[0, -1]]
        cmin, cmax = np.where(cols)[0][[0, -1]]
        ds_prod.close()

        grupos_necesarios = [
            'PRODUCT',
            'PRODUCT/SUPPORT_DATA/GEOLOCATIONS',
            'PRODUCT/SUPPORT_DATA/INPUT_DATA',
            'PRODUCT/SUPPORT_DATA/DETAILED_RESULTS'
        ]
        
        modo_escritura = 'w' 
        for grupo in grupos_necesarios:
            try:
                ds = xr.open_dataset(input_path, group=grupo, engine='netcdf4')
                ds_recortado = ds.isel(scanline=slice(rmin, rmax+1), ground_pixel=slice(cmin, cmax+1))
                ds_recortado.to_netcdf(output_path, group=grupo, mode=modo_escritura, engine='netcdf4')
                ds.close()
                modo_escritura = 'a' 
            except OSError:
                pass # Ignorar si el grupo no existe en el crudo original

        return True
    except Exception as e:
        print(f"      [Error al recortar con xarray]: {e}")
        return False

def procesar_orbita(product_id, product_name, token):
    """Descarga el archivo temporal, aplica el recorte y elimina el crudo."""
    nombre_base = product_name.replace('.nc', '')
    nc_final = os.path.join(CARPETA_DESTINO, f"{nombre_base}_reduced_chile.nc")
    nc_temporal = os.path.join(CARPETA_DESTINO, f"{nombre_base}_temp.nc")
    
    if os.path.exists(nc_final):
        print(f"    -> Ya existe el recorte para esta órbita. Omitiendo.")
        return nc_final

    print(f"    -> Descargando temporalmente (~500 MB)...")
    d_url = f"https://catalogue.dataspace.copernicus.eu/odata/v1/Products({product_id})/$value"
    
    try:
        s = requests.Session()
        res = s.get(d_url, headers={"Authorization": f"Bearer {token}"}, allow_redirects=False)
        while res.status_code in (301, 302, 303, 307):
            res = s.get(res.headers['Location'], headers={"Authorization": f"Bearer {token}"}, allow_redirects=False)
            
        with s.get(res.url, headers={"Authorization": f"Bearer {token}"}, stream=True) as r:
            r.raise_for_status() 
            with open(nc_temporal, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192): f.write(chunk)
                    
        tamano_mb = os.path.getsize(nc_temporal) / (1024 * 1024)
        if tamano_mb < UMBRAL_MIN_MB:
            print(f"    -> [DESCARTADO] Órbita original muy pequeña ({tamano_mb:.1f} MB).")
            os.remove(nc_temporal)
            return None
        
        print(f"    -> Recortando matriz de la órbita ({tamano_mb:.1f} MB)...")
        exito = recortar_nc(nc_temporal, nc_final)
        
        if os.path.exists(nc_temporal):
            os.remove(nc_temporal)
            
        if exito: 
            print(f"    -> [EXITO] Archivo reducido generado.")
            return nc_final
        else: 
            print(f"    -> [VACÍO] Órbita sin píxeles válidos para el BBOX.")
            return None
            
    except Exception as e:
        print(f"    -> Error en descarga/recorte: {e}")
        if os.path.exists(nc_temporal): os.remove(nc_temporal)
        return None

# ==========================================
# EJECUCIÓN PRINCIPAL
# ==========================================
if __name__ == "__main__":
    fecha_actual = datetime.strptime(FECHA_INICIO, "%Y-%m-%d")
    fecha_final = datetime.strptime(FECHA_FIN, "%Y-%m-%d")
    bbox_poly = box(LON_LIMITS[0], LAT_LIMITS[0], LON_LIMITS[1], LAT_LIMITS[1])

    while fecha_actual <= fecha_final:
        dia_str = fecha_actual.strftime("%Y-%m-%d")
        print(f"\n=====================================\n PROCESANDO DÍA: {dia_str}\n=====================================")
        
        try:
            token = get_token()
            productos_crudos = buscar_productos_dia(dia_str)
            
            candidatos = []
            for p in productos_crudos:
                start_time_block = p['Name'].split('____')[1].split('_')[0]
                if any(hora in start_time_block for hora in ['T17', 'T18', 'T19']):
                    raw_footprint = p.get('Footprint', '')
                    if raw_footprint:
                        clean_wkt = raw_footprint.split(';')[-1].strip("'") if ';' in raw_footprint else raw_footprint
                        try:
                            orbit_poly = wkt.loads(clean_wkt)
                            intersection_area = bbox_poly.intersection(orbit_poly).area
                            if intersection_area > 0:
                                p['intersection_area'] = intersection_area
                                candidatos.append(p)
                        except Exception as e:
                            print(f"  [!] Error parseando footprint: {e}")

            if not candidatos:
                print(f"  -> No hubo pasadas centrales válidas.")
            else:
                candidatos.sort(key=lambda x: x['intersection_area'], reverse=True)
                print(f"  -> Encontrados {len(candidatos)} candidatos. Priorizando la órbita maestra...")
                
                for i, p in enumerate(candidatos, 1):
                    print(f"  [{i}/{len(candidatos)}] Evaluando: {p['Name']} (Área de traslape: {p['intersection_area']:.2f})")
                    ruta_archivo = procesar_orbita(p['Id'], p['Name'], token)
                    
                    if ruta_archivo:
                        print(f"  -> [CONFIRMADO] Órbita maestra asegurada. Saltando pasadas marginales.")
                        break

        except Exception as e:
            print(f"Error procesando el día {dia_str}: {e}")
            
        fecha_actual += timedelta(days=1)

    print("\n¡Pipeline finalizado! Datos listos para tu análisis en NetCDF4.")
