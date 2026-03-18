from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from django.core.cache import cache
from django.db.models import Count, Max
from django.db.models.functions import TruncDate
from django.utils import timezone

# Create your views here.
from django.views.generic import FormView, TemplateView
from plotly.subplots import make_subplots

from config.settings import GLOBAL_SETTINGS, BASE_DIR
from config.utils import day_ahead_to_agile

from .forms import ForecastForm
from .models import AgileData, ForecastData, Forecasts, History, PriceHistory

regions = GLOBAL_SETTINGS["REGIONS"]
PRIOR_DAYS = 2


class GlossaryView(TemplateView):
    template_name = "base.html"


class ColorView(TemplateView):
    template_name = "color_mode.html"


class ApiHowToView(TemplateView):
    template_name = "api_how_to.html"


class AboutView(TemplateView):
    template_name = "about.html"


class HomeAssistantView(TemplateView):
    template_name = "home_assistant.html"


class StatsView(TemplateView):
    template_name = "stats.html"

    @staticmethod
    def _latest(model, field: str = "date_time"):
        return model.objects.aggregate(latest=Max(field))["latest"]

    @staticmethod
    def _readiness_state(current: int, target: int) -> str:
        if current >= target:
            return "ready"
        if current >= max(1, int(target * 0.5)):
            return "growing"
        return "cold"

    @staticmethod
    def _daily_counts(model, ts_field: str, now, days: int = 7) -> list[int]:
        start = (now - pd.Timedelta(days=days - 1)).date()
        end = now.date()
        day_index = pd.date_range(start=start, end=end, freq="D").date

        rows = (
            model.objects.filter(**{f"{ts_field}__date__gte": start})
            .annotate(day=TruncDate(ts_field))
            .values("day")
            .annotate(c=Count("id"))
            .order_by("day")
        )
        by_day = {r["day"]: r["c"] for r in rows}
        return [int(by_day.get(d, 0)) for d in day_index]

    def _build_growth_context(self) -> dict:
        now = timezone.now()
        day_ago = now - pd.Timedelta("24h")

        counts = {
            "forecasts": Forecasts.objects.count(),
            "forecast_data": ForecastData.objects.count(),
            "agile_data": AgileData.objects.count(),
            "price_history": PriceHistory.objects.count(),
            "history": History.objects.count(),
        }

        growth_24h = {
            "forecasts": Forecasts.objects.filter(created_at__gte=day_ago).count(),
            "forecast_data": ForecastData.objects.filter(date_time__gte=day_ago).count(),
            "agile_data": AgileData.objects.filter(date_time__gte=day_ago).count(),
            "price_history": PriceHistory.objects.filter(date_time__gte=day_ago).count(),
            "history": History.objects.filter(date_time__gte=day_ago).count(),
        }

        latest = {
            "forecast": self._latest(Forecasts, "created_at"),
            "forecast_data": self._latest(ForecastData),
            "agile_data": self._latest(AgileData),
            "price_history": self._latest(PriceHistory),
            "history": self._latest(History),
        }

        freshness = {}
        for key, ts in latest.items():
            if ts is None:
                freshness[key] = {"age_minutes": None, "state": "missing", "label": "Missing"}
                continue
            age_minutes = int((now - ts).total_seconds() // 60)
            if age_minutes <= 90:
                state, label = "fresh", "Fresh"
            elif age_minutes <= 180:
                state, label = "warn", "Aging"
            else:
                state, label = "stale", "Stale"
            freshness[key] = {"age_minutes": age_minutes, "state": state, "label": label}

        daily_labels = pd.date_range(end=now.date(), periods=7, freq="D").strftime("%d %b").tolist()
        daily_counts = {
            "forecasts": self._daily_counts(Forecasts, "created_at", now, days=7),
            "forecast_data": self._daily_counts(ForecastData, "date_time", now, days=7),
            "price_history": self._daily_counts(PriceHistory, "date_time", now, days=7),
            "history": self._daily_counts(History, "date_time", now, days=7),
        }

        trend_figure = go.Figure()
        trend_figure.add_trace(
            go.Scatter(x=daily_labels, y=daily_counts["forecasts"], mode="lines+markers", name="Forecast runs")
        )
        trend_figure.add_trace(
            go.Scatter(x=daily_labels, y=daily_counts["forecast_data"], mode="lines+markers", name="Feature rows")
        )
        trend_figure.add_trace(
            go.Scatter(x=daily_labels, y=daily_counts["price_history"], mode="lines+markers", name="Price rows")
        )
        trend_figure.add_trace(
            go.Scatter(x=daily_labels, y=daily_counts["history"], mode="lines+markers", name="History rows")
        )
        trend_figure.update_layout(
            height=360,
            margin={"l": 30, "r": 10, "t": 20, "b": 20},
            template="plotly_dark",
            plot_bgcolor="#212529",
            paper_bgcolor="#343a40",
            legend={"orientation": "h", "y": 1.15, "x": 0},
            yaxis={"title": "Rows / day"},
        )

        # Practical readiness checks used by the ML training gate.
        targets = {
            "forecasts": 2,
            "forecast_data": 50,
            "price_history": 50,
            "joined_rows_proxy": 30,
        }

        joined_rows_proxy = min(counts["forecast_data"], counts["price_history"])

        readiness = {
            "forecasts": {
                "current": counts["forecasts"],
                "target": targets["forecasts"],
                "state": self._readiness_state(counts["forecasts"], targets["forecasts"]),
                "pct": min(100, int((counts["forecasts"] / targets["forecasts"]) * 100)),
            },
            "forecast_data": {
                "current": counts["forecast_data"],
                "target": targets["forecast_data"],
                "state": self._readiness_state(counts["forecast_data"], targets["forecast_data"]),
                "pct": min(100, int((counts["forecast_data"] / targets["forecast_data"]) * 100)),
            },
            "price_history": {
                "current": counts["price_history"],
                "target": targets["price_history"],
                "state": self._readiness_state(counts["price_history"], targets["price_history"]),
                "pct": min(100, int((counts["price_history"] / targets["price_history"]) * 100)),
            },
            "joined_rows_proxy": {
                "current": joined_rows_proxy,
                "target": targets["joined_rows_proxy"],
                "state": self._readiness_state(joined_rows_proxy, targets["joined_rows_proxy"]),
                "pct": min(100, int((joined_rows_proxy / targets["joined_rows_proxy"]) * 100)),
            },
        }

        readiness_ok = all(v["current"] >= v["target"] for v in readiness.values())
        freshness_ok = all(v["state"] == "fresh" for v in freshness.values() if v["state"] != "missing")
        readiness_rows = [
            {"label": "Forecast runs", **readiness["forecasts"]},
            {"label": "Feature rows", **readiness["forecast_data"]},
            {"label": "Price rows", **readiness["price_history"]},
            {"label": "Joined rows (proxy)", **readiness["joined_rows_proxy"]},
        ]

        freshness_rows = [
            {"label": "Forecast runs", **freshness["forecast"]},
            {"label": "Feature rows", **freshness["forecast_data"]},
            {"label": "Price rows", **freshness["price_history"]},
            {"label": "History rows", **freshness["history"]},
        ]

        return {
            "counts": counts,
            "growth_24h": growth_24h,
            "latest": latest,
            "readiness": readiness,
            "readiness_rows": readiness_rows,
            "readiness_ok": readiness_ok,
            "freshness_rows": freshness_rows,
            "freshness_ok": freshness_ok,
            "trend_plot": trend_figure.to_html(full_html=False, include_plotlyjs=False),
        }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["growth"] = self._build_growth_context()

        # If there is no historical price data yet, render growth/readiness cards only.
        if not PriceHistory.objects.exists():
            context["stats"] = None
            context["plot_files"] = []
            return context

        agile_actuals_end = pd.Timestamp(PriceHistory.objects.all().order_by("-date_time")[0].date_time)
        agile_actuals_start = agile_actuals_end - pd.Timedelta("7D")

        agile_actuals_objects = PriceHistory.objects.filter(date_time__gt=agile_actuals_start).order_by("date_time")
        df = pd.DataFrame(
            index=[obj.date_time for obj in agile_actuals_objects],
            data={"actuals": [obj.agile for obj in agile_actuals_objects]},
        )

        agile_forecast_data = AgileData.objects.filter(
            date_time__gt=agile_actuals_start, date_time__lte=agile_actuals_end
        )
        figure = make_subplots(
            rows=2,
            cols=1,
            subplot_titles=("Agile Price", "Error HeatMap"),
            shared_xaxes=True,
            vertical_spacing=0.05,
        )

        for forecast in agile_forecast_data.values_list("forecast").distinct():
            forecast_created_at = pd.Timestamp(Forecasts.objects.filter(id=forecast[0])[0].created_at).tz_convert("GB")
            forecast_after = (
                pd.Timestamp.combine(forecast_created_at.date(), pd.Timestamp("22:00").time())
                .tz_localize("UTC")
                .tz_convert("GB")
            )

            if forecast_created_at.hour >= 16:
                forecast_after += pd.Timedelta("24h")

            agile_pred_objects = agile_forecast_data.filter(forecast=forecast[0])
            index = [
                obj.date_time
                for obj in agile_pred_objects
                if forecast_after < obj.date_time < forecast_after + pd.Timedelta("7D")
            ]
            data = [
                obj.agile_pred
                for obj in agile_pred_objects
                if forecast_after < obj.date_time < forecast_after + pd.Timedelta("7D")
            ]
            if len(data) > 0:
                df.loc[index, forecast_created_at] = data
                figure.add_trace(
                    go.Scatter(
                        x=df.index,
                        y=df[forecast_created_at],
                        line={"color": "grey", "width": 0.5},
                        showlegend=False,
                        mode="lines",
                    ),
                )

        figure.add_trace(
            go.Scatter(
                x=df.index,
                y=df["actuals"],
                line={"color": "yellow", "width": 3},
                showlegend=False,
            ),
        )

        layout = dict(
            yaxis={"title": "Agile Price [p/kWh]"},
            margin={
                "r": 5,
                "t": 50,
            },
            height=800,
            template="plotly_dark",
        )

        figure.update_layout(**layout)
        figure.update_layout(
            plot_bgcolor="#212529",
            paper_bgcolor="#343a40",
        )

        for x in df.columns[1:]:
            df[x] = abs(df[x] - df["actuals"])
        df_to_plot = df.drop(["actuals"], axis=1).sort_index(axis=1).T
        df_to_plot = df_to_plot.loc[df_to_plot.index > agile_actuals_start - pd.Timedelta("3D")]
        x = df_to_plot.columns
        y = df_to_plot.index
        z = df_to_plot.to_numpy()

        figure.add_heatmap(x=x, y=y, z=z, row=2, col=1, colorbar={"title": "Error\n[p/kWh]"})

        # HTML for the existing Plotly figure
        context["stats"] = figure.to_html(full_html=False, include_plotlyjs="cdn")

        # --- SECTION 2: Static Diagnostic PNG Plots ---
        plot_dir = BASE_DIR / "plots" / "stats_plots"

        # context["plot_files"] = [f"stats_plots/{f.name}" for f in plot_dir.glob("*.png") if f.is_file()]

        descriptions = {
            "1_actual_vs_predicted_over_time.png": (
                "This plot shows the full training dataset used for the last forecast. Actual data are plotted as the black line."
                + " The fitted data from the trained mode are plotted in red and should generally overlay the black. Forecasts generated"
                + " from the model using prior data are plotted as the points with the colour indicating the lead time from forecast to actual pricing."
                + "All of the plots below other than the XGBoost Feature Importance show the same data in different ways.",
                "Actual vs Predicted Over Time",
            ),
            "2_scatters.png": (
                "Scatter plot of predicted vs actual prices. Color shows forecast lead time (in days).",
                "Prediction vs Actual Scatter",
            ),
            "3_residuals.png": (
                "Histogram of prediction errors (residuals) to visualize model bias and spread.",
                "Residuals Distribution",
            ),
            "4_kde_error_by_horizon.png": (
                "KDE heatmap showing how forecast error varies by lead time. Initially the data are biased towards shorted lead times but as the database"
                + "grows this bias should reduce. The distribution is, however, always expected to be tighter over short lead times.",
                "Forecast Error by Horizon (KDE)",
            ),
            "5_feature_importance.png": (
                "This plot is slightly different to the others in that it shows the relative importance of the various inputs in building the regression model. Details of each feauture can be found on the About page.",
                "XGBoost Feature Importance",
            ),
        }

        plot_dir = BASE_DIR / "plots" / "stats_plots"
        plot_files = [
            {
                "filename": f"stats_plots/{f.name}",
                "description": descriptions.get(f.name, ("", ""))[0],
                "title": descriptions.get(f.name, ("", ""))[1] or f.name.replace("_", " ").title().replace(".Png", ""),
            }
            for f in plot_dir.glob("*.png")
            if f.is_file()
        ]

        context["plot_files"] = plot_files

        return context


class GraphFormView(FormView):
    form_class = ForecastForm
    template_name = "graph.html"

    def get_form_kwargs(self):
        kwargs = super(GraphFormView, self).get_form_kwargs()
        # kwargs["region"] = self.kwargs.get("region", "X").upper()
        kwargs["prefix"] = "test"
        # print(kwargs)
        return kwargs

    def update_chart(self, context, **kwargs):
        region = context["region"]
        if region not in regions:
            region = "X"
        forecasts_to_plot = kwargs.get("forecasts_to_plot")
        days_to_plot = int(kwargs.get("days_to_plot", 7))
        show_generation_and_demand = kwargs.get("show_generation_and_demand", True)
        show_range = kwargs.get("show_range_on_most_recent_forecast", True)
        show_overlap = kwargs.get("show_forecast_overlap", False)
        # print(">>> views.py | GraphFormView | update_chart")
        # print(forecasts_to_plot)

        first_forecast = Forecasts.objects.filter(id__in=forecasts_to_plot).order_by("-created_at")[0]
        # print(f"First Forecast: {first_forecast}")
        first_forecast_data = ForecastData.objects.filter(forecast=first_forecast).order_by("date_time")
        forecast_start = first_forecast_data[0].date_time
        # print(f"Forecast Start: {forecast_start}")
        if len(first_forecast_data) >= 48 * days_to_plot:
            forecast_end = first_forecast_data[48 * days_to_plot].date_time
        else:
            forecast_end = [d.date_time for d in first_forecast_data][-1]

        # print(f"Forecast End: {forecast_end}")
        price_start = PriceHistory.objects.all().order_by("-date_time")[48 * PRIOR_DAYS].date_time
        # print(f"Price Start: {price_start}")

        start = min(forecast_start, price_start)

        data = []
        p = PriceHistory.objects.filter(date_time__gte=start).order_by("-date_time")

        day_ahead = pd.Series(index=[a.date_time for a in p], data=[a.day_ahead for a in p])
        agile = day_ahead_to_agile(day_ahead, region=region).sort_index()

        hover_template_time_price = "%{x|%H:%M}<br>%{y:.2f}p/kWh"
        hover_template_price = "%{y:.2f}p/kWh"

        data = data + [
            go.Scatter(
                x=agile.loc[:forecast_end].index.tz_convert("GB"),
                y=agile.loc[:forecast_end],
                marker={"symbol": 104, "size": 1, "color": "white"},
                mode="lines",
                name="Actual",
                hovertemplate=hover_template_price,
            )
        ]

        limit = None
        width = 3
        for f in Forecasts.objects.filter(id__in=forecasts_to_plot).order_by("-created_at"):
            d = AgileData.objects.filter(forecast=f, region=region)
            if len(d) > 0:
                if limit is None:
                    d = d[: (48 * days_to_plot)]
                    limit = d[-1].date_time
                    # print(limit)
                else:
                    d = list(d.filter(date_time__lte=limit))

                x = [a.date_time for a in d if (a.date_time >= agile.index[-1] or show_overlap)]
                y = [a.agile_pred for a in d if (a.date_time >= agile.index[-1] or show_overlap)]

                df = pd.Series(index=pd.to_datetime(x), data=y).sort_index()
                try:
                    df.index = df.index.tz_convert("GB")
                except:
                    df.index = df.index.tz_localize("GB")

                df = df.loc[agile.index[0] :]

                data = data + [
                    go.Scatter(
                        x=df.index,
                        y=df,
                        marker={"symbol": 104, "size": 10},
                        mode="lines",
                        line=dict(width=width),
                        name=f"Prediction ({pd.to_datetime(f.name).tz_localize('GB').strftime('%d-%b %H:%M')})",
                        hovertemplate=hover_template_price,
                    )
                ]

                if (width == 3) and (d[0].agile_high != d[0].agile_low and show_range):
                    data = data + [
                        go.Scatter(
                            x=df.index,
                            y=[a.agile_low for a in d if (a.date_time >= agile.index[-1] or show_overlap)],
                            marker={"symbol": 104, "size": 10},
                            mode="lines",
                            line=dict(width=1, color="red"),
                            name="Low",
                            showlegend=False,
                            hovertemplate=hover_template_price,
                        ),
                        go.Scatter(
                            x=df.index,
                            y=[a.agile_high for a in d if (a.date_time >= agile.index[-1] or show_overlap)],
                            marker={"symbol": 104, "size": 10},
                            mode="lines",
                            line=dict(width=1, color="red"),
                            name="High",
                            showlegend=False,
                            fill="tonexty",
                            fillcolor="rgba(255,127,127,0.5)",
                            hovertemplate=hover_template_price,
                        ),
                    ]
                width = 1

        if show_generation_and_demand:
            figure = make_subplots(
                rows=2,
                cols=1,
                subplot_titles=("Agile Price", "Generation and Demand"),
                shared_xaxes=True,
                vertical_spacing=0.1,
            )

            height = 800
            legend = dict(orientation="h", yanchor="top", y=-0.075, xanchor="right", x=1)

            f = Forecasts.objects.filter(id__in=forecasts_to_plot).order_by("-created_at")[0]
            print(forecast_end)
            d = ForecastData.objects.filter(forecast=f, date_time__lte=forecast_end).order_by("date_time")
            print([a.date_time for a in d][-1])
            figure.add_trace(
                go.Scatter(
                    x=[a.date_time for a in d],
                    y=[(a.demand + a.solar + a.emb_wind) / 1000 for a in d],
                    line={"color": "cyan", "width": 3},
                    name="Forecast National Demand",
                ),
                row=2,
                col=1,
            )

            figure.add_trace(
                go.Scatter(
                    x=[a.date_time for a in d],
                    y=[a.bm_wind / 1000 for a in d],
                    fill="tozeroy",
                    line={"color": "rgba(63,127,63)"},
                    fillcolor="rgba(127,255,127,0.8)",
                    name="Forecast Metered Wind",
                ),
                row=2,
                col=1,
            )

            figure.add_trace(
                go.Scatter(
                    x=[a.date_time for a in d],
                    y=[(a.emb_wind + a.bm_wind) / 1000 for a in d],
                    fill="tonexty",
                    line={"color": "blue", "width": 1},
                    fillcolor="rgba(127,127,255,0.8)",
                    name="Forecast Embedded Wind",
                ),
                row=2,
                col=1,
            )

            figure.add_trace(
                go.Scatter(
                    x=[a.date_time for a in d],
                    y=[(a.solar + a.emb_wind + a.bm_wind) / 1000 for a in d],
                    fill="tonexty",
                    line={"color": "lightgray", "width": 3},
                    fillcolor="rgba(255,255,127,0.8)",
                    name="Forecast Solar",
                ),
                row=2,
                col=1,
            )

            h = History.objects.filter(date_time__gte=start, date_time__lte=forecast_end)

            figure.add_trace(
                go.Scatter(
                    x=[a.date_time for a in h],
                    y=[(a.demand + a.solar + (a.total_wind - a.bm_wind)) / 1000 for a in h],
                    line={"color": "#aaaa77", "width": 2},
                    name="Historic Demand",
                ),
                row=2,
                col=1,
            )
            figure.add_trace(
                go.Scatter(
                    x=[a.date_time for a in h],
                    y=[(a.total_wind + a.solar) / 1000 for a in h],
                    line={"color": "red", "width": 2},
                    name="Historic Solar + Wind",
                ),
                row=2,
                col=1,
            )
            figure.update_xaxes(row=1, col=1, showticklabels=True)

        else:
            legend = dict(orientation="h", yanchor="top", y=-0.15, xanchor="right", x=1)
            height = 400
            figure = make_subplots(
                rows=1,
                cols=1,
            )

        for d in data:
            figure.append_trace(d, row=1, col=1)

        layout = dict(
            yaxis={"title": "Agile Price [p/kWh]"},
            margin={
                "r": 5,
                "t": 50,
            },
            legend=legend,
            height=height,
            template="plotly_dark",
            hovermode="x",
        )

        figure.update_layout(**layout)
        figure.update_layout(
            plot_bgcolor="#212529",
            paper_bgcolor="#343a40",
        )
        figure.update_yaxes(
            title_text="Agile Price [p/kWh]",
            row=1,
            col=1,
            fixedrange=True,
        )
        figure.update_yaxes(
            title_text="Power [GW]",
            row=2,
            col=1,
            fixedrange=True,
        )
        figure.update_xaxes(
            tickformatstops=[
                dict(dtickrange=[None, 86000000], value="%H:%M<br>%a %d %b"),
                dict(dtickrange=[86000000, None], value="%H:%M<br>%a %d %b"),
            ],
            # fixedrange=True,
        )

        context["graph"] = figure.to_html(
            config={
                "modeBarButtonsToRemove": [
                    "zoom",
                    "pan",
                    "select",
                    "zoomIn",
                    "zoomOut",
                    "autoScale",
                    "resetScale",
                ]
            }
        )

        return context

    def get_context_data(self, **kwargs):
        # print(">>>views.py | GraphFormView | get_context_data")
        context = super().get_context_data(**kwargs)
        # context["form2"] = OptionsForm()
        f = Forecasts.objects.latest("created_at")
        region = self.kwargs.get("region", "X").upper()
        context["region"] = region
        context["region_name"] = regions.get(region, {"name": ""})["name"]
        # print(context)

        context = self.update_chart(context=context, forecasts_to_plot=[f.id])
        return context

    def form_valid(self, form):
        # print(">>>views.py | GraphFormView | form_valid")
        # print(form.cleaned_data)
        context = self.get_context_data(form=form)
        context = self.update_chart(context=context, **form.cleaned_data)

        return self.render_to_response(context=context)

    def form2_valid(self, form):
        # print(">>>views.py | GraphFormView | form_valid")
        # print(form.cleaned_data)
        context = self.get_context_data(form=form)
        context = self.update_chart(context=context, **form.cleaned_data)

        return self.render_to_response(context=context)
