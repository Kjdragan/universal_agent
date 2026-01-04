# Memory System Documentation

This documentation provides comprehensive information about the Universal Agent Memory System. Navigate through the different sections to understand the architecture, usage, and development guidelines.

## 📚 Documentation Structure

### 📖 [README.md](README.md)
**Main Documentation** - Overview and quick start guide
- System overview and architecture
- Memory types and storage strategies
- Integration with main agent
- Basic usage patterns

### 🏗️ [ARCHITECTURE.md](ARCHITECTURE.md)
**Architecture Deep Dive** - Technical architecture details
- System design philosophy
- Component interaction diagrams
- Memory flow architecture
- Performance characteristics
- Scaling considerations
- Security and resilience strategies

### 👨‍💻 [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md)
**Developer Guide** - For developers working with the memory system
- Getting started and setup
- Code structure deep dive
- Debugging and troubleshooting
- Testing strategies
- Performance optimization
- Best practices and contribution guidelines

### 💡 [USAGE_EXAMPLES.md](USAGE_EXAMPLES.md)
**Usage Examples** - Practical examples and patterns
- Basic usage examples
- Advanced usage patterns
- Integration examples
- Error handling examples
- Performance testing examples

### 🧭 [003_MEMORY_SYSTEM_STATUS_AND_LETTA_PATH.md](003_MEMORY_SYSTEM_STATUS_AND_LETTA_PATH.md)
**Current Status** - PRD implementation status and Letta pivot
- In-house memory system status
- Letta integration rationale
- Compatibility shim details
- Criteria for returning to the PRD plan

## 🎯 Key Concepts

### Memory Types
1. **Core Memory** - SQLite-based, always in context
   - `persona`: Agent identity and personality
   - `human`: User preferences and facts
   - `system_rules`: Technical constraints

2. **Archival Memory** - ChromaDB-based, long-term storage
   - Semantic search capabilities
   - Tagged metadata
   - Vector embeddings

### Storage Architecture
```
Memory_System/data/
├── agent_core.db              # SQLite - Core Memory
└── chroma_db/                 # ChromaDB - Archival Memory
    └── chroma.sqlite3        # Vector embeddings & metadata
```

## 🚀 Quick Start

### Basic Usage
```python
from Memory_System.manager import MemoryManager

# Initialize
mem_mgr = MemoryManager()

# Get system prompt
context = mem_mgr.get_system_prompt_addition()

# Update memory
mem_mgr.core_memory_replace("human", "Name: Alice\nRole: Developer")

# Add to archival
mem_mgr.archival_memory_insert("User prefers Python", tags="preference")
```

### Tool Definitions
The system exposes these tools to agents:
- `core_memory_replace` - Overwrite memory blocks
- `core_memory_append` - Append to memory blocks
- `archival_memory_insert` - Save to long-term storage
- `archival_memory_search` - Semantic search archival memory

## 🔧 Troubleshooting

### Common Issues
- **Import Errors**: Ensure `Memory_System` is in Python path
- **Permission Issues**: Check storage directory permissions
- **ChromaDB Issues**: Reset by deleting `chroma_db` directory
- **Memory Corruption**: Reinitialize from defaults

### Debugging Techniques
- Enable logging for SQLite and ChromaDB operations
- Inspect databases directly with SQL tools
- Use memory inspection functions
- Check performance metrics

## 📊 Performance Characteristics

| Operation | Latency | Storage |
|-----------|---------|---------|
| Core Memory Read | <1ms | SQLite |
| Core Memory Write | <5ms | SQLite |
| Archival Insert | ~100-500ms | ChromaDB |
| Semantic Search | ~200-1000ms | ChromaDB |

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Write tests for new functionality
4. Follow coding standards
5. Update documentation
6. Submit a pull request

### Development Commands
```bash
# Run tests
python -m pytest tests/test_memory_system.py

# Format code
python -m black Memory_System/
python -m isort Memory_System/

# Type checking
python -m mypy Memory_System/
```

## 📞 Support

For questions or issues:
1. Check the troubleshooting section
2. Review the developer guide
3. Run the test suite
4. Check existing documentation
5. Create an issue with detailed description

---

**Next Steps**:
1. Start with [README.md](README.md) for an overview
2. Dive into [ARCHITECTURE.md](ARCHITECTURE.md) for technical details
3. Follow [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md) for development
4. Explore [USAGE_EXAMPLES.md](USAGE_EXAMPLES.md) for practical patterns
