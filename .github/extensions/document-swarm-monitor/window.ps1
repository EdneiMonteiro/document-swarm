[CmdletBinding()]
param(
  [Parameter(Mandatory)][ValidateRange(1,2147483647)][int]$OwnerPid,
  [Parameter(Mandatory)][ValidateLength(1,260)][string]$WindowTitle,
  [Parameter(Mandatory)][ValidateSet('compact','expanded','inspect')][string]$Mode,
  [ValidateRange(0,10)][int]$WaitSeconds = 8
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$owner = Get-Process -Id $OwnerPid -ErrorAction Stop
if ($owner.ProcessName -ne 'copilot') { throw 'Window owner is not the Copilot canvas host.' }

Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

public static class SwarmWindow {
    public delegate bool Enumerate(IntPtr handle, IntPtr parameter);
    [StructLayout(LayoutKind.Sequential)]
    public struct Rect { public int Left, Top, Right, Bottom; }
    [StructLayout(LayoutKind.Sequential)]
    public struct Monitor {
        public int Size;
        public Rect Bounds;
        public Rect Work;
        public uint Flags;
    }
    public sealed class Measurement {
        public int owner_pid;
        public int dpi;
        public int client_width;
        public int client_height;
        public int outer_width;
        public int outer_height;
        public double css_width;
        public double css_height;
        public string mode;
        public bool fitted;
        public bool minimized;
    }
    [DllImport("user32.dll", SetLastError=true)]
    static extern bool EnumWindows(Enumerate callback, IntPtr parameter);
    [DllImport("user32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern int GetWindowText(IntPtr handle, StringBuilder text, int capacity);
    [DllImport("user32.dll")]
    static extern bool IsWindowVisible(IntPtr handle);
    [DllImport("user32.dll")]
    static extern bool IsZoomed(IntPtr handle);
    [DllImport("user32.dll")]
    static extern bool IsIconic(IntPtr handle);
    [DllImport("user32.dll")]
    static extern uint GetWindowThreadProcessId(IntPtr handle, out uint processId);
    [DllImport("user32.dll", SetLastError=true)]
    static extern bool GetWindowRect(IntPtr handle, out Rect rect);
    [DllImport("user32.dll", SetLastError=true)]
    static extern bool GetClientRect(IntPtr handle, out Rect rect);
    [DllImport("user32.dll")]
    static extern uint GetDpiForWindow(IntPtr handle);
    [DllImport("user32.dll", SetLastError=true)]
    static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
    [DllImport("user32.dll")]
    static extern IntPtr MonitorFromWindow(IntPtr handle, uint flags);
    [DllImport("user32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern bool GetMonitorInfo(IntPtr handle, ref Monitor info);
    [DllImport("user32.dll", SetLastError=true)]
    static extern bool SetWindowPos(IntPtr handle, IntPtr after, int x, int y, int width, int height, uint flags);
    [DllImport("user32.dll")]
    static extern bool ShowWindow(IntPtr handle, int command);

    static bool Matches(IntPtr handle, int owner, string title) {
        uint actual;
        GetWindowThreadProcessId(handle, out actual);
        if (actual != owner || !IsWindowVisible(handle)) return false;
        var text = new StringBuilder(512);
        GetWindowText(handle, text, text.Capacity);
        return String.Equals(text.ToString(), title, StringComparison.Ordinal);
    }
    static IntPtr Find(int owner, string title, int waitSeconds) {
        var deadline = DateTime.UtcNow.AddSeconds(waitSeconds);
        do {
            IntPtr found = IntPtr.Zero;
            int count = 0;
            if (!EnumWindows((handle, parameter) => {
                if (Matches(handle, owner, title)) { found = handle; count++; }
                return true;
            }, IntPtr.Zero)) throw new Win32Exception(Marshal.GetLastWin32Error());
            if (count > 1) throw new InvalidOperationException("Ambiguous canvas window; refusing to resize.");
            if (count == 1) return found;
            if (DateTime.UtcNow >= deadline) break;
            Thread.Sleep(100);
        } while (true);
        throw new InvalidOperationException("Exact canvas window not found for this process and title.");
    }
    static Measurement Read(IntPtr handle, int owner, string title, string mode) {
        if (!Matches(handle, owner, title)) throw new InvalidOperationException("Canvas identity changed.");
        Rect client, outer;
        if (!GetClientRect(handle, out client) || !GetWindowRect(handle, out outer))
            throw new Win32Exception(Marshal.GetLastWin32Error());
        uint dpi = GetDpiForWindow(handle);
        if (dpi == 0) throw new InvalidOperationException("Canvas DPI is unavailable.");
        return new Measurement {
            owner_pid = owner, dpi = (int)dpi, mode = mode, minimized = IsIconic(handle),
            client_width = client.Right - client.Left, client_height = client.Bottom - client.Top,
            outer_width = outer.Right - outer.Left, outer_height = outer.Bottom - outer.Top,
            css_width = (client.Right - client.Left) * 96.0 / dpi,
            css_height = (client.Bottom - client.Top) * 96.0 / dpi
        };
    }
    public static Measurement Fit(int owner, string title, string mode, int waitSeconds) {
        IntPtr previous = SetThreadDpiAwarenessContext(new IntPtr(-4));
        if (previous == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error());
        try {
            IntPtr handle = Find(owner, title, waitSeconds);
            var before = Read(handle, owner, title, mode);
            if (mode == "inspect") return before;
            if (before.minimized) throw new InvalidOperationException("Canvas is minimized; restore it before resizing.");
            if (IsZoomed(handle)) {
                ShowWindow(handle, 9);
                Thread.Sleep(50);
                before = Read(handle, owner, title, mode);
            }
            var monitor = new Monitor();
            monitor.Size = Marshal.SizeOf(typeof(Monitor));
            if (!GetMonitorInfo(MonitorFromWindow(handle, 2), ref monitor))
                throw new Win32Exception(Marshal.GetLastWin32Error());
            Rect outer;
            if (!GetWindowRect(handle, out outer)) throw new Win32Exception(Marshal.GetLastWin32Error());
            int borderWidth = before.outer_width - before.client_width;
            int borderHeight = before.outer_height - before.client_height;
            int workWidth = monitor.Work.Right - monitor.Work.Left;
            int workHeight = monitor.Work.Bottom - monitor.Work.Top;
            int width = mode == "compact" ? (int)Math.Round(720.0 * before.dpi / 96) : (int)(workWidth * .9) - borderWidth;
            int height = mode == "compact" ? (int)Math.Round(480.0 * before.dpi / 96) : (int)(workHeight * .9) - borderHeight;
            width = Math.Min(width, workWidth - borderWidth);
            height = Math.Min(height, workHeight - borderHeight);
            if (width < 1 || height < 1) throw new InvalidOperationException("Monitor work area is invalid.");
            int x = Math.Max(monitor.Work.Left, Math.Min(outer.Left, monitor.Work.Right - width - borderWidth));
            int y = Math.Max(monitor.Work.Top, Math.Min(outer.Top, monitor.Work.Bottom - height - borderHeight));
            if (!Matches(handle, owner, title)) throw new InvalidOperationException("Canvas identity changed.");
            if (!SetWindowPos(handle, IntPtr.Zero, x, y, width + borderWidth, height + borderHeight, 0x0014))
                throw new Win32Exception(Marshal.GetLastWin32Error());
            for (int attempt = 0; attempt < 20; attempt++) {
                Thread.Sleep(25);
                var measured = Read(handle, owner, title, mode);
                if (Math.Abs(measured.client_width - width) <= 2 && Math.Abs(measured.client_height - height) <= 2) {
                    measured.fitted = true;
                    return measured;
                }
            }
            throw new InvalidOperationException("Canvas did not accept the requested client size.");
        } finally {
            SetThreadDpiAwarenessContext(previous);
        }
    }
}
'@

[SwarmWindow]::Fit($OwnerPid, $WindowTitle, $Mode, $WaitSeconds) | ConvertTo-Json -Compress
