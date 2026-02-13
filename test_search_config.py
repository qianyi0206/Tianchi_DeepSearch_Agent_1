
import asyncio
from dotenv import load_dotenv
from deepresearch.tools.search_tool import build_searcher

load_dotenv()

async def main():
    print("\n====== 测试 SerpApi 配置 ======")
    searcher = build_searcher()
    print(f"当前使用的搜索器: {type(searcher).__name__}")
    
    # 检查 Key 是否正确加载
    if hasattr(searcher, 'api_key'):
        masked_key = searcher.api_key[:4] + "..." + searcher.api_key[-4:]
        print(f"已加载 Key: {masked_key}")
    else:
        print("警告: 未检测到 api_key 属性，可能回退到了 DuckDuckGo")

    query = "2024年巴黎奥运会金牌榜第一名"
    print(f"\n正在尝试搜索: {query}")
    print("等待响应 (如果是 SerpApi，这可能需要几秒钟)...")
    
    results = await searcher.search(query)
    
    if results:
        print(f"\n✅ 成功！搜到 {len(results)} 条结果:")
        for i, r in enumerate(results[:3]):
            print(f"{i+1}. {r.title} ({r.url})")
    else:
        print("\n❌ 搜索结果为空！可能原因：")
        print("1. 网络不通 (SerpApi 服务器在美国)")
        print("2. Key 无效或额度耗尽")
        print("3. 代理配置问题")

if __name__ == "__main__":
    asyncio.run(main())
