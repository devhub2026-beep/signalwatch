// SignalWatch.exe —— 桌面启动器。
//
// 它自己不做业务，只干三件事：找到 Python、判断是不是已经开着一个、
// 用 pythonw 在后台把界面拉起来。找不到 Python 就弹一个能看懂的框，不闪退。
//
// 编译：csc /target:winexe /win32icon:..\assets\app.ico /out:SignalWatch.exe SignalWatch.cs

using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Windows.Forms;

static class Launcher
{
    const string MutexName = "SignalWatch.Desktop.SingleInstance";
    const string Title = "SignalWatch";

    [STAThread]
    static int Main(string[] args)
    {
        string appDir = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar);
        string mode = (args.Length > 0 && args[0].ToLowerInvariant() == "serve") ? "serve" : "desktop";

        bool first;
        using (var mutex = new System.Threading.Mutex(true, MutexName, out first))
        {
            if (!first && mode == "desktop")
            {
                // 已经开着一个窗口了，双击第二次不给它开第二个
                return 0;
            }
            try
            {
                string python = FindPython(appDir, mode);
                if (python == null)
                {
                    Fail(appDir, "找不到 Python 运行环境。\r\n\r\n"
                        + "把 pythonw.exe 的路径写进本目录下的 python.txt（一行），\r\n"
                        + "或者把 Python 装到 PATH 里，然后再双击我。");
                    return 2;
                }

                var psi = new ProcessStartInfo
                {
                    FileName = python,
                    Arguments = "\"" + Path.Combine(appDir, "run.py") + "\" " + mode,
                    WorkingDirectory = appDir,
                    UseShellExecute = false,
                    CreateNoWindow = true,
                };
                psi.EnvironmentVariables["SIGNALWATCH_HOME"] = Path.Combine(appDir, "data");
                psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";

                Process.Start(psi);
                return 0;
            }
            catch (Exception ex)
            {
                Fail(appDir, "启动失败：" + ex.Message);
                return 1;
            }
        }
    }

    static string FindPython(string appDir, string mode)
    {
        string exeName = (mode == "serve") ? "python.exe" : "pythonw.exe";
        string plain = (mode == "serve") ? "python.exe" : "python.exe";

        string[] candidates = new string[]
        {
            Path.Combine(appDir, "runtime", exeName),
            Path.Combine(appDir, "runtime", plain),
        };
        foreach (var c in candidates)
        {
            if (File.Exists(c)) return c;
        }

        string txt = Path.Combine(appDir, "python.txt");
        if (File.Exists(txt))
        {
            foreach (var line in File.ReadAllLines(txt, Encoding.UTF8))
            {
                string p = line.Trim().Trim('"');
                if (p.Length == 0 || p.StartsWith("#")) continue;
                if (Directory.Exists(p)) p = Path.Combine(p, exeName);
                if (File.Exists(p)) return p;
            }
        }

        string env = Environment.GetEnvironmentVariable("SIGNALWATCH_PYTHON");
        if (!string.IsNullOrEmpty(env) && File.Exists(env)) return env;

        string[] known =
        {
            @"E:\.venv\FastAPIProject7\.venv\Scripts\" + exeName,
            @"E:\.venv\FastAPIProject7\.venv\Scripts\" + plain,
        };
        foreach (var k in known)
        {
            if (File.Exists(k)) return k;
        }

        // 最后问 PATH
        try
        {
            var psi = new ProcessStartInfo("where", exeName)
            {
                RedirectStandardOutput = true,
                UseShellExecute = false,
                CreateNoWindow = true,
            };
            using (var p = Process.Start(psi))
            {
                string outp = p.StandardOutput.ReadToEnd();
                p.WaitForExit(3000);
                foreach (var line in outp.Split('\n'))
                {
                    string t = line.Trim();
                    if (t.Length > 0 && File.Exists(t)) return t;
                }
            }
        }
        catch { }

        return null;
    }

    static void Fail(string appDir, string message)
    {
        try
        {
            File.AppendAllText(
                Path.Combine(appDir, "launcher.log"),
                DateTime.Now.ToString("s") + "  " + message.Replace("\r\n", " ") + Environment.NewLine,
                Encoding.UTF8);
        }
        catch { }
        MessageBox.Show(message, Title + " 打不开", MessageBoxButtons.OK, MessageBoxIcon.Warning);
    }
}
