"""Pure viewport math and clipping, independent of Tk and hardware."""
import math

METRICS = {'temperature': ('温度', 'K', .5), 'resistance': ('电阻', 'Ω', .05), 'power': ('交流有功功率', 'W', .02)}
PALETTE = ('#2376be','#d04b36','#218a50','#8b4db8','#b27800','#008b99','#c14489','#596171')


def finite(value):
    return isinstance(value, (float, int)) and math.isfinite(value)


def bounds_for(series, margin):
    points = [(t,v) for _,_,samples in series for t,v in samples if finite(t) and finite(v)]
    if not points:
        return (0., 60., 0., 1.)
    t0,t1 = min(t for t,v in points),max(t for t,v in points)
    if t1 <= t0:
        t1 = t0+1.
    lo,hi = min(v for t,v in points),max(v for t,v in points)
    pad = max((hi-lo)*.08,margin)
    return t0,t1,lo-pad,hi+pad


def validate_bounds(values):
    result = tuple(float(v) for v in values)
    if len(result)!=4 or not all(math.isfinite(v) for v in result):
        raise ValueError('坐标范围必须是四个有限数字')
    if result[0]>=result[1] or result[2]>=result[3]:
        raise ValueError('坐标下限必须小于上限')
    if not all(math.isfinite(span) and span>=1e-12 for span in (result[1]-result[0],result[3]-result[2])):
        raise ValueError('坐标跨度过大或过小')
    return result


def zoom(bounds, factor, x_fraction=.5, y_fraction=.5):
    x0,x1,y0,y1 = bounds
    cx,cy = x0+(x1-x0)*x_fraction,y0+(y1-y0)*y_fraction
    return validate_bounds((cx+(x0-cx)*factor,cx+(x1-cx)*factor,
                            cy+(y0-cy)*factor,cy+(y1-cy)*factor))


def pan(bounds, dx, dy):
    x0,x1,y0,y1 = bounds
    return validate_bounds((x0+dx,x1+dx,y0+dy,y1+dy))


def clipped_lines(samples, bounds, rect):
    """Clip before drawing; invalid samples break the line, rather than hide gaps."""
    x0,x1,y0,y1 = bounds
    left,top,right,bottom = rect
    previous = None
    group = []
    for t,v in samples:
        if not finite(t) or not finite(v):
            if group:yield group
            group=[];previous=None
            continue
        current=(t,v)
        if previous is not None:
            a,b=previous;dx,dy=t-a,v-b
            low,high=0.,1.
            for p,q in ((-dx,a-x0),(dx,x1-a),(-dy,b-y0),(dy,y1-b)):
                if p==0:
                    if q<0:low,high=1.,0.;break
                elif p<0:low=max(low,q/p)
                else:high=min(high,q/p)
            if low<=high:
                segment=[]
                for f in (low,high):
                    segment.extend((left+(a+f*dx-x0)/(x1-x0)*(right-left),
                                    bottom-(b+f*dy-y0)/(y1-y0)*(bottom-top)))
                if group and group[-2:]==segment[:2]:group.extend(segment[2:])
                else:
                    if group:yield group
                    group=segment
            elif group:
                yield group;group=[]
        previous=current
    if group:yield group
