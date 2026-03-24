"""Training data health monitoring schemas for operator visibility."""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class DataSourceBreakdown(BaseModel):
    """Breakdown of price data sources used for ML training."""
    agile_actual_count: int = Field(description="Number of actual Agile prices")
    nordpool_count: int = Field(description="Number of Nordpool day-ahead prices")
    total_count: int = Field(description="Total price points available")
    agile_percent: float = Field(description="Percentage of Agile actuals")
    earliest_date: Optional[datetime] = Field(description="Earliest price date")
    latest_date: Optional[datetime] = Field(description="Latest price date")
    coverage_days: int = Field(description="Days of price coverage")


class WeatherCoverageInfo(BaseModel):
    """Historical weather data coverage information."""
    backfill_forecasts: int = Field(description="Number of backfill forecast records")
    earliest_weather: Optional[datetime] = Field(description="Earliest weather data")
    latest_weather: Optional[datetime] = Field(description="Latest weather data")
    coverage_days: int = Field(description="Days of weather coverage")
    agile_without_weather_days: int = Field(description="Days of Agile prices without weather data")


class TrainingDataAlert(BaseModel):
    """Actionable alert about training data issues."""
    severity: str = Field(description="critical | warning | info")
    title: str = Field(description="Brief alert title")
    message: str = Field(description="Detailed problem description")
    impact: str = Field(description="Impact on system performance")
    fix_action: Optional[str] = Field(description="How operator can fix this")
    fix_endpoint: Optional[str] = Field(description="API endpoint to call for fix")


class TrainingDataHealthResponse(BaseModel):
    """Comprehensive training data health status for operator dashboard."""
    generated_at: datetime
    region: str
    
    # Overall status
    health_status: str = Field(description="healthy | degraded | critical")
    health_summary: str = Field(description="One-line status summary")
    
    # Data sources
    price_data: DataSourceBreakdown
    weather_coverage: WeatherCoverageInfo
    
    # Training metrics
    training_points: int = Field(description="Total joined training rows")
    forecast_count: int = Field(description="Number of forecast records")
    meets_minimum_threshold: bool = Field(description="Meets minimum training data requirement")
    minimum_threshold: int = Field(description="Minimum recommended training points")
    
    # Actionable alerts
    alerts: list[TrainingDataAlert] = Field(default_factory=list)
    
    # Recommendations
    recommendations: list[str] = Field(default_factory=list)
