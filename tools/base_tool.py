# tools/base_tool.py
# -*- coding: utf-8 -*-

from abc import ABC, abstractmethod

class BaseTool(ABC):
    """
    Abstract Base Class for all external data collection tools.
    
    This class defines the interface that any new tool (e.g., StockCollector,
    NewsCollector) must implement. This ensures modularity and interchangeability
    within the main application.
    """

    @abstractmethod
    def run(self, input_data: str) -> str:
        """
        Executes the tool's main logic.
        
        Args:
            input_data (str): The input required by the tool (e.g., an address, a stock ticker).
            
        Returns:
            str: The collected data as a formatted string, ready for AI injection.
        """
        pass

    @abstractmethod
    def get_description(self) -> str:
        """
        Returns a short description of the tool for logging/UI purposes.
        """
        pass