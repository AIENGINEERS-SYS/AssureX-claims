import React from 'react';
import {Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart,
  Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis} from 'recharts';

const colors = ['#0c604d', '#badb57', '#e8b555', '#8ba5b5', '#b7cac0', '#d28e88'];
const label = value => String(value ?? '').replaceAll('_', ' ').replace(/\b\w/g, c => c.toUpperCase());

export default function Chart({variant, values, series, keys, type}) {
  if (variant === 'distribution') {
    const data = Object.entries(values || {}).filter(([, value]) => Number(value) > 0).map(([name, value]) => ({name, value}));
    if (!data.length) return <div className="empty">Metrics will appear when records are available.</div>;
    return <><div className="chart-wrap" role="img" aria-label={data.map(d => `${d.name}: ${d.value}`).join(', ')}><ResponsiveContainer width="100%" height="100%">
      {type === 'bar' ? <BarChart data={data} margin={{top: 10, right: 12, left: -22, bottom: 18}}><CartesianGrid stroke="#edf1e9" vertical={false}/>
        <XAxis dataKey="name" tick={{fontSize: 10}} interval={0} angle={-15} textAnchor="end" height={42}/><YAxis allowDecimals={false} tick={{fontSize: 10}}/>
        <Tooltip/><Bar dataKey="value" radius={[5, 5, 0, 0]}>{data.map((d, i) => <Cell key={d.name} fill={colors[i % colors.length]}/>)}</Bar></BarChart> :
        <PieChart><Pie data={data} innerRadius={57} outerRadius={89} paddingAngle={3} dataKey="value" nameKey="name">
          {data.map((d, i) => <Cell key={d.name} fill={colors[i % colors.length]}/>)}</Pie><Tooltip/></PieChart>}
    </ResponsiveContainer></div><div className="chart-key">{data.map((item, i) => <span key={item.name}><b style={{background: colors[i % colors.length]}}/>{item.name} · {item.value}</span>)}</div></>;
  }
  if (!series?.length || !series.some(item => keys.some(key => item[key])))
    return <div className="empty">No recorded activity in this period.</div>;
  const Component = type === 'area' ? AreaChart : LineChart;
  return <div className="chart-wrap" role="img" aria-label={`${keys.map(label).join(' and ')} over time`}><ResponsiveContainer width="100%" height="100%"><Component data={series} margin={{top: 12, right: 15, left: -23, bottom: 4}}>
    <CartesianGrid stroke="#edf1e9" vertical={false}/><XAxis dataKey="date" tickFormatter={v => v.slice(5)} minTickGap={28} tick={{fontSize: 10}}/>
    <YAxis allowDecimals={false} tick={{fontSize: 10}}/><Tooltip/><Legend/>
    {keys.map((key, i) => type === 'area' ? <Area key={key} type="monotone" name={label(key)} dataKey={key} stroke={colors[i]} fill={colors[i]} fillOpacity={.12} strokeWidth={2}/> :
      <Line key={key} type="monotone" name={label(key)} dataKey={key} stroke={colors[i]} strokeWidth={2.5} dot={false}/>)}
  </Component></ResponsiveContainer></div>;
}
