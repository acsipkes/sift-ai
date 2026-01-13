# tools/neighborhood_collector.py
# -*- coding: utf-8 -*-

import requests
import logging
from typing import Dict, List, Optional, Any
from .base_tool import BaseTool
from core.version import APP_NAME, TOOL_2_VERSION

class NeighborhoodCollector(BaseTool):
    """
    Tool for analyzing the amenities and infrastructure around a specific address
    using OpenStreetMap (Nominatim and Overpass API).
    """

    def __init__(self) -> None:
        """
        Initializes the collector with specific headers to comply with OSM policies.
        """
        self.headers: Dict[str, str] = {
            'User-Agent': f'{APP_NAME}_Neighborhood_Analyzer/{TOOL_2_VERSION} (student_project_portfolio)',
            'Accept-Language': 'en' 
        }

    def get_description(self) -> str:
        """
        Returns a short description of the tool.

        Returns:
            str: The tool description.
        """
        return "Neighborhood Analyzer (OpenStreetMap)"

    def run(self, input_data: str) -> str:
        """
        Main execution method to analyze the neighborhood.

        Args:
            input_data (str): The address to analyze.
            
        Returns:
            str: The collected data as a formatted string or error message.
        """
        address = input_data
        coords = self._get_coordinates(address)
        
        if not coords:
            return "ERROR: The provided address could not be found on the map."
        
        return self._fetch_overpass_data(coords['lat'], coords['lon'])

    def _get_coordinates(self, address: str) -> Optional[Dict[str, Any]]:
        """
        Fetches logical coordinates for a given address using Nominatim API.

        Args:
            address (str): The search query address.

        Returns:
            Optional[Dict[str, Any]]: A dictionary containing lat, lon, and name, or None if failed.
        """
        try:
            url = "https://nominatim.openstreetmap.org/search"
            # Timeout is essential for external requests
            resp = requests.get(
                url, 
                params={'q': address, 'format': 'json', 'limit': 1}, 
                headers=self.headers, 
                timeout=10
            )
            data = resp.json()
            if data:
                return {
                    'lat': float(data[0]['lat']), 
                    'lon': float(data[0]['lon']), 
                    'name': data[0]['display_name']
                }
        except Exception as e:
            logging.error(f"Geo-location error: {e}")
        return None

    def _fetch_overpass_data(self, lat: float, lon: float, radius: int = 500) -> str:
        """
        Queries the Overpass API for amenities around the coordinates.

        Args:
            lat (float): Latitude.
            lon (float): Longitude.
            radius (int): Search radius in meters. Defaults to 500.

        Returns:
            str: Formatted string of results or error message.
        """
        query = f"""
        [out:json][timeout:25];
        (
          node["amenity"~"pub|bar|cafe|fast_food|pharmacy"](around:{radius},{lat},{lon});
          node["shop"~"supermarket|convenience|tobacco"](around:{radius},{lat},{lon});
          node["leisure"~"park|playground"](around:{radius},{lat},{lon});
          node["highway"~"bus_stop|primary|secondary"](around:{radius},{lat},{lon});
          node["railway"~"tram_stop"](around:{radius},{lat},{lon});
        );
        out body;
        """
        try:
            resp = requests.post(
                "https://overpass-api.de/api/interpreter", 
                data=query, 
                headers=self.headers, 
                timeout=25
            )
            data = resp.json().get('elements', [])
            return self._format_data(data, lat, lon)
        except Exception as e:
            return f"Data fetch error: {e}"

    def _format_data(self, elements: List[Dict[str, Any]], lat: float, lon: float) -> str:
        """
        Formats the raw API data into a readable string.

        Args:
            elements (List[Dict[str, Any]]): List of raw element dictionaries from API.
            lat (float): Latitude of the center point.
            lon (float): Longitude of the center point.

        Returns:
            str: Organized text summary of the neighborhood.
        """
        lines = [f"ANALYZED COORDINATES: {lat}, {lon}\n"]
        categories: Dict[str, List[str]] = {
            "NOISE_SOURCES": [], 
            "CONVENIENCE": [], 
            "GREEN_AREAS": [], 
            "TRANSPORT": []
        }
        
        for el in elements:
            tags = el.get('tags', {})
            name = tags.get('name', 'Unnamed')
            kind = tags.get('amenity') or tags.get('shop') or tags.get('highway') or tags.get('leisure')
            
            entry = f"- {name} ({kind})"
            
            if kind in ['pub', 'bar', 'fast_food', 'tobacco', 'primary', 'secondary']: 
                categories["NOISE_SOURCES"].append(entry)
            elif kind in ['supermarket', 'convenience', 'pharmacy', 'cafe']: 
                categories["CONVENIENCE"].append(entry)
            elif kind in ['park', 'playground']: 
                categories["GREEN_AREAS"].append(entry)
            elif kind in ['bus_stop', 'tram_stop']: 
                categories["TRANSPORT"].append(entry)
        
        for cat, items in categories.items():
            if items:
                lines.append(f"\n[{cat}]")
                lines.extend(items[:8]) # Limit items to save tokens
                if len(items) > 8: 
                    lines.append(f"...and {len(items)-8} more")
                
        return "\n".join(lines) if len(lines) > 1 else "No relevant places found nearby."