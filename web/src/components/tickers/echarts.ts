/**
 * ECharts with only the pieces the ticker timeline uses (scatter markers, one
 * line, grids, tooltips, shaded "not loaded yet" areas, canvas). Importing "echarts/core" plus named modules
 * instead of all of "echarts" keeps the download small. This file is loaded
 * with a dynamic import() from the browser only, so the server never runs it.
 */
import { LineChart, ScatterChart } from "echarts/charts";
import { GridComponent, MarkAreaComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([LineChart, ScatterChart, GridComponent, MarkAreaComponent, TooltipComponent, CanvasRenderer]);

export { echarts };
