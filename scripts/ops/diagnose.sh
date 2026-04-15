#!/bin/bash
# 诊断安全隔离 + 显示完整验证结果
[ "$(id -u)" -ne 0 ] && { echo "sudo bash $0"; exit 1; }

cd /tmp

echo "=== 诊断: claw_agent 访问 /Users/zayl ==="
echo "whoami:"
sudo -H -u claw_agent bash --norc --noprofile -c 'whoami'
echo ""
echo "ls /Users/zayl/ 输出:"
sudo -H -u claw_agent bash --norc --noprofile -c 'export HOME=/Users/claw_agent; ls /Users/zayl/ 2>&1; echo "EXIT=$?"'
echo ""
echo "ls -la /Users/ (看权限):"
ls -la /Users/ | grep -E "zayl|claw_agent|ollama"
echo ""
echo "id claw_agent:"
sudo -H -u claw_agent bash --norc --noprofile -c 'id'
echo ""
echo "=== ACL 检查 ==="
ls -led /Users/zayl
echo ""

echo "=== 移除 ACL 并重新检查 ==="
chmod -N /Users/zayl 2>/dev/null || true
chmod 700 /Users/zayl
ls -led /Users/zayl
echo ""
echo "移除 ACL 后再次测试:"
sudo -H -u claw_agent bash --norc --noprofile -c 'export HOME=/Users/claw_agent; ls /Users/zayl/ 2>&1; echo "EXIT=$?"'
