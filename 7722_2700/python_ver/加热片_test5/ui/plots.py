"""Real-time plotting mixin."""


class PlotMixin:
    def refresh_plot_channels(self):
        choices = ['全部通道'] + list(self.table.get_children())
        self.plot_channel_combo.configure(values=choices)
        if self.plot_channel.get() not in choices:
            self.plot_channel.set('全部通道')
        self.draw()

    def draw(self):
        if not hasattr(self, 'plot_channel') or not self.curves_page.winfo_ismapped():
            return
        channels = sorted(int(item) for item in self.table.get_children())
        selected = self.plot_channel.get()
        palette = ('#2376be', '#d04b36', '#218a50', '#8b4db8', '#b27800',
                   '#008b99', '#c14489', '#596171', '#7d761d', '#3f579c')
        visible = [ch for ch in channels if ch in self.history and (selected == '全部通道' or selected == str(ch))]
        power_selected = self.plot_tabs.select() == str(self.plot_canvases['power'].master)
        self.plot_channel_combo.configure(state='disabled' if power_selected else 'readonly')
        for key, canvas in self.plot_canvases.items():
            canvas.delete('all')
            w, h = canvas.winfo_width(), canvas.winfo_height()
            if w < 150 or h < 65:
                continue
            series = []
            if key == 'power':
                unit, minimum_margin = 'W', .02
                if self.power_history:
                    series.append(('power_measured', '7722 实测有功功率', '#218a50', (), list(self.power_history)))
            else:
                column, unit = (1, 'K') if key == 'temperature' else (2, 'Ω')
                minimum_margin = .5 if column == 1 else .05
                for ch in visible:
                    index = channels.index(ch)
                    color = palette[index % len(palette)]
                    dash = () if index < len(palette) else (3+index//len(palette), 2)
                    series.append((f'channel_{ch}', str(ch), color, dash,
                                   [(point[0], point[column]) for point in self.history[ch]
                                    if point[column] is not None]))
            series = [item for item in series if item[4]]
            if not series:
                canvas.create_text(w/2, h/2, text='等待采集数据…', fill='#64748b')
                continue
            values = [point[1] for _, _, _, _, points in series for point in points]
            times = [point[0] for _, _, _, _, points in series for point in points]
            lo, hi = min(values), max(values)
            margin = max((hi-lo)*.08, minimum_margin)
            lo, hi = lo-margin, hi+margin
            t0, t1 = min(times), max(times)
            span = max(t1-t0, 1e-6)
            left, right, top, bottom = 78, w-15, 30, h-28
            def x(t):
                return left+(t-t0)/span*(right-left)
            def y(v):
                return bottom-(v-lo)/(hi-lo)*(bottom-top)
            for fraction in (0., .5, 1.):
                v = lo+fraction*(hi-lo)
                canvas.create_line(left, y(v), right, y(v), fill='#dde3ea')
                canvas.create_text(left-7, y(v), anchor='e', text=f'{v:.2f}{unit}', fill='#475569')
            canvas.create_line(left, top, left, bottom, right, bottom, fill='#94a3b8')
            canvas.create_text(left, h-10, anchor='w', text=f'{t0:.1f}s', fill='#475569')
            canvas.create_text(right, h-10, anchor='e', text=f'{t1:.1f}s', fill='#475569')
            canvas.create_text((left+right)/2, h-10, text='运行时间 (s)', fill='#475569')
            legend_x = left
            for tag, label, color, dash, samples in series:
                points = [coordinate for t, value in samples for coordinate in (x(t), y(value))]
                if len(points) >= 4:
                    canvas.create_line(*points, fill=color, width=2, dash=dash, tags=(tag,))
                else:
                    px, py = points
                    canvas.create_oval(px-2, py-2, px+2, py+2, fill=color, outline=color, tags=(tag,))
                if legend_x+65 < right:
                    canvas.create_line(legend_x, 13, legend_x+18, 13, fill=color, width=2, dash=dash)
                    canvas.create_text(legend_x+23, 13, anchor='w', text=label, fill=color)
                    legend_x += 200 if key == 'power' else 70
                elif legend_x < right:
                    canvas.create_text(legend_x, 13, anchor='w', text='…', fill='#475569')
                    legend_x = right
