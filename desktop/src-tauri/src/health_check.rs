use std::time::Duration;
use anyhow::{Result, anyhow};

/// 检查指定端口的 HTTP 服务是否就绪
pub async fn check_health(port: u16) -> Result<()> {
    let url = format!("http://127.0.0.1:{}/api/system/health", port);
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(3))
        .build()?;
    let resp = client.get(&url).send().await?;
    if resp.status().is_success() {
        Ok(())
    } else {
        Err(anyhow!("健康检查失败: HTTP {}", resp.status()))
    }
}

/// 等待服务就绪，超时返回错误
pub async fn wait_for_ready(port: u16, timeout: Duration) -> Result<()> {
    let start = std::time::Instant::now();
    let mut last_error = String::new();

    while start.elapsed() < timeout {
        match check_health(port).await {
            Ok(()) => return Ok(()),
            Err(e) => {
                last_error = e.to_string();
                tokio::time::sleep(Duration::from_millis(500)).await;
            }
        }
    }

    Err(anyhow!("服务启动超时（{}秒）: {}", timeout.as_secs(), last_error))
}
